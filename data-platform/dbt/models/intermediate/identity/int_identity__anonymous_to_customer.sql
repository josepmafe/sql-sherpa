{#-
    Incremental with `merge`: dbt updates the rows whose anonymous_id already exists and inserts the rest,
    so a run only has to emit the devices that have new events. The watermark is the table's own
    max(last_received_at), which makes the model self-correcting: no run date is involved.
-#}
{{
    config(
        materialized='incremental',
        incremental_strategy='merge',
        unique_key='anonymous_id',
        on_schema_change='fail',
        indexes=[{'columns': ['anonymous_id'], 'unique': True}]
    )
}}

with

identified_events as (

    select
        anonymous_id,
        customer_id,
        event_id,
        occurred_at,
        received_at
    from {{ ref('stg_events__events') }}
    where
        customer_id is not null
        {% if is_incremental() %}
            and received_at > (
                select
                    coalesce(
                        max(watermark.last_received_at),
                        '-infinity'::timestamptz
                    )
                from {{ this }} as watermark
            )
        {% endif %}

),

-- Per device, the latest identification wins; ties go to the highest event_id.
ranked as (

    select
        *,
        row_number() over (
            partition by anonymous_id order by occurred_at desc, event_id desc
        ) as identification_rank,
        max(received_at) over (partition by anonymous_id) as last_received_at
    from identified_events

),

latest as (

    select
        anonymous_id,
        customer_id,
        occurred_at as identified_at,
        event_id as identifying_event_id,
        last_received_at
    from ranked
    where identification_rank = 1

),

-- The table's own rows: a full build has none.
previous as (

    {% if is_incremental() %}
        select * from {{ this }}
    {% else %}
        select
            null::text as anonymous_id,
            null::bigint as customer_id,
            null::timestamptz as identified_at,
            null::text as identifying_event_id,
            null::timestamptz as last_received_at
        where false
    {% endif %}

),

-- A late but older identification must not override a newer one, so each new
-- winner is compared with the device's previous row; either way
-- last_received_at advances.
compared as (

    select
        latest.anonymous_id,
        latest.customer_id as new_customer_id,
        latest.identified_at as new_identified_at,
        latest.identifying_event_id as new_event_id,
        previous.customer_id as old_customer_id,
        previous.identified_at as old_identified_at,
        previous.identifying_event_id as old_event_id,
        greatest(
            latest.last_received_at, previous.last_received_at
        ) as last_received_at,
        coalesce(
            (latest.identified_at, latest.identifying_event_id)
            > (previous.identified_at, previous.identifying_event_id),
            true
        ) as is_newer
    from latest
    left join previous
        on latest.anonymous_id = previous.anonymous_id

)

select
    anonymous_id,
    last_received_at,
    case when is_newer then new_customer_id else old_customer_id end
        as customer_id,
    case when is_newer then new_identified_at else old_identified_at end
        as identified_at,
    case when is_newer then new_event_id else old_event_id end
        as identifying_event_id
from compared
