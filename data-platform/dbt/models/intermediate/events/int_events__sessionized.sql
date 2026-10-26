{#-
    Incremental with `delete+insert` on session_date: dbt deletes every row whose session_date the run
    emits, then inserts the run's rows, so a run rewrites whole days of sessions. The watermark is the
    table's own max(max_received_at), as in the device map. Two marts read the table; the project's
    analyze hook covers marts only, so it has its own.
-#}
{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='session_date',
        on_schema_change='fail',
        indexes=[{'columns': ['session_date']}],
        post_hook="analyze {{ this }}"
    )
}}

with

-- The first session day an incremental run rewrites (spec 6.4). A new event
-- (received after the watermark) can join a session that started the day
-- before it happened, across midnight, so the cutoff is the day before the
-- earliest new event (this holds while no session lasts longer than a day).
-- Without new events the cutoff is null and the run emits nothing. A full
-- build rewrites every day.
cutoff as (

    {% if is_incremental() %}
        select (min(occurred_at) at time zone 'UTC')::date - 1 as cutoff_date
        from {{ ref('stg_events__events') }}
        where
            received_at > (
                select
                    coalesce(
                        max(watermark.max_received_at),
                        '-infinity'::timestamptz
                    )
                from {{ this }} as watermark
            )
    {% else %}
        select '-infinity'::date as cutoff_date
    {% endif %}

),

-- Reading starts a day before the cutoff, so a session that runs from that
-- day into the cutoff day is seen whole: it starts before the cutoff and is
-- not emitted, instead of coming out cut as a session of the cutoff day.
events as (

    select * from {{ ref('stg_events__events') }}
    where
        occurred_at >= (
            select (cutoff.cutoff_date - 1)::timestamp at time zone 'UTC'
            from cutoff
        )

),

customers as (

    select * from {{ ref('stg_shop__customers') }}

),

channel_mapping as (

    select * from {{ ref('channel_mapping') }}

),

test_flagged as (

    select
        events.*,
        coalesce(customers.is_test_account, false) as is_test_event
    from events
    left join customers
        on events.customer_id = customers.customer_id

),

-- A gap greater than the timeout since the previous event of the same
-- anonymous_id starts a new session. The first event has no previous one.
flagged as (

    select
        *,
        coalesce(
            occurred_at - lag(occurred_at) over (
                partition by anonymous_id order by occurred_at asc, event_id asc
            ) > make_interval(mins => {{ var('session_timeout_minutes') }}),
            true
        ) as is_session_start
    from test_flagged

),

-- The running count of session starts numbers each anonymous_id's sessions.
numbered as (

    select
        *,
        sum(is_session_start::int) over (
            partition by anonymous_id
            order by occurred_at asc, event_id asc
        ) as session_number
    from flagged

),

-- Every event takes the values of its session's first event.
sessions_marked as (

    select
        *,
        first_value(occurred_at) over session_window as session_start_at,
        first_value(utm_source) over session_window as session_utm_source,
        first_value(utm_medium) over session_window as session_utm_medium,
        first_value(utm_campaign) over session_window as session_utm_campaign,
        first_value(referrer) over session_window as session_referrer,
        max(received_at) over (
            partition by anonymous_id, session_number
        ) as max_received_at,
        bool_or(is_test_event) over (
            partition by anonymous_id, session_number
        ) as is_test_session
    from numbered
    window session_window as (
        partition by anonymous_id, session_number
        order by occurred_at asc, event_id asc
        rows between unbounded preceding and unbounded following
    )

),

sessions as (

    select
        event_id,
        anonymous_id,
        customer_id,
        event_type,
        occurred_at,
        received_at,
        order_id,
        device_type,
        page_path,
        session_start_at,
        session_utm_source,
        session_utm_medium,
        session_utm_campaign,
        session_referrer,
        max_received_at,
        -- epoch microseconds, not a text cast: the cast would depend on the
        -- connection's time zone.
        md5(
            anonymous_id
            || '|'
            || (extract(epoch from session_start_at) * 1000000)::bigint::text
        ) as session_id,
        (session_start_at at time zone 'UTC')::date as session_date
    from sessions_marked
    where not is_test_session

),

sessionized as (

    select
        sessions.*,
        case
            when channel_mapping.marketing_channel is not null
                then channel_mapping.marketing_channel
            when sessions.session_utm_source is not null
                then 'unknown'
            when
                sessions.session_referrer
                ~* '^https?://([^/]*\.)?(google|bing|duckduckgo)\.'
                then 'organic'
            when sessions.session_referrer is not null
                then 'referral'
            else 'direct'
        end as marketing_channel
    from sessions
    left join channel_mapping
        on
            sessions.session_utm_source = channel_mapping.utm_source
            and sessions.session_utm_medium = channel_mapping.utm_medium

)

select * from sessionized
where session_date >= (select cutoff.cutoff_date from cutoff)
