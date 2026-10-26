{{
    config(
        indexes=[
            {'columns': ['session_date']},
            {'columns': ['customer_id']},
        ]
    )
}}

with

events as (

    select * from {{ ref('int_events__sessionized') }}

),

device_map as (

    select * from {{ ref('int_identity__anonymous_to_customer') }}

),

customers as (

    select * from {{ ref('stg_shop__customers') }}

),

campaigns as (

    select * from {{ ref('stg_marketing__campaigns') }}

),

-- One row per session. The landing page and device come from the first event
-- (occurred_at, then event_id), and a session with several purchases (a second
-- order placed in the same visit) keeps the first one. The session-level
-- columns are the same on every event of the session, so min() picks them.
session_aggregates as (

    select
        session_id,
        min(anonymous_id) as anonymous_id,
        min(session_date) as session_date,
        min(session_start_at) as session_start_at,
        max(occurred_at) as session_end_at,
        count(*) as event_count,
        count(*) filter (where event_type = 'page_view') as page_view_count,
        (
            array_agg(page_path order by occurred_at asc, event_id asc)
        )[1] as landing_page_path,
        (
            array_agg(device_type order by occurred_at asc, event_id asc)
        )[1] as device_type,
        min(session_utm_source) as utm_source,
        min(session_utm_medium) as utm_medium,
        min(session_utm_campaign) as utm_campaign,
        min(marketing_channel) as marketing_channel,
        bool_or(event_type = 'product_view') as viewed_product,
        bool_or(event_type = 'add_to_cart') as added_to_cart,
        bool_or(event_type = 'begin_checkout') as began_checkout,
        bool_or(event_type = 'purchase') as purchased,
        (
            array_agg(
                order_id order by occurred_at asc, event_id asc
            ) filter (where event_type = 'purchase')
        )[1] as order_id
    from events
    group by session_id

),

-- Test accounts (R5) are excluded, by the customer the device is stitched to.
stitched as (

    select
        session_aggregates.*,
        device_map.customer_id
    from session_aggregates
    left join device_map
        on session_aggregates.anonymous_id = device_map.anonymous_id
    left join customers
        on device_map.customer_id = customers.customer_id
    where not coalesce(customers.is_test_account, false)

),

-- The visitor is the stitched customer, otherwise the device.
visitors as (

    select
        *,
        extract(
            epoch from session_end_at - session_start_at
        )::integer as duration_seconds,
        row_number() over (
            partition by
                customer_id,
                case when customer_id is null then anonymous_id end
            order by session_start_at asc, session_id asc
        ) = 1 as is_first_session
    from stitched

),

final as (

    select
        visitors.session_id::text as session_id,
        visitors.anonymous_id::text as anonymous_id,
        visitors.customer_id::bigint as customer_id,
        visitors.session_date::date as session_date,
        visitors.session_start_at::timestamptz as session_start_at,
        visitors.session_end_at::timestamptz as session_end_at,
        visitors.duration_seconds,
        visitors.event_count::integer as event_count,
        visitors.page_view_count::integer as page_view_count,
        visitors.landing_page_path::text as landing_page_path,
        visitors.device_type::text as device_type,
        visitors.utm_source::text as utm_source,
        visitors.utm_medium::text as utm_medium,
        visitors.utm_campaign::text as utm_campaign,
        visitors.marketing_channel::text as marketing_channel,
        campaigns.campaign_id::bigint as campaign_id,
        visitors.is_first_session,
        visitors.viewed_product,
        visitors.added_to_cart,
        visitors.began_checkout,
        visitors.purchased,
        visitors.order_id::bigint as order_id
    from visitors
    left join campaigns
        on visitors.utm_campaign = campaigns.utm_campaign

)

select * from final
