with

ad_spend as (

    select * from {{ ref('stg_marketing__ad_spend_daily') }}

),

campaigns as (

    select * from {{ ref('dim_campaigns') }}

),

sessions as (

    select * from {{ ref('fct_sessions') }}

),

orders as (

    select * from {{ ref('fct_orders') }}
    where not is_cancelled

),

-- An order counts on the day its session started, not the day it was placed.
session_activity as (

    select
        sessions.campaign_id,
        sessions.session_date,
        count(distinct sessions.session_id) as sessions,
        count(orders.order_id) as orders,
        sum(orders.net_revenue) as attributed_net_revenue,
        count(orders.order_id) filter (where orders.is_first_order)
            as new_customers
    from sessions
    left join orders
        on sessions.session_id = orders.session_id
    where sessions.campaign_id is not null
    group by sessions.campaign_id, sessions.session_date

),

final as (

    select
        ad_spend.campaign_id::bigint as campaign_id,
        ad_spend.spend_date::date as spend_date,
        campaigns.channel::text as channel,
        ad_spend.spend::numeric(12, 2) as spend,
        ad_spend.impressions,
        ad_spend.clicks,
        coalesce(session_activity.sessions, 0)::integer as sessions,
        coalesce(session_activity.orders, 0)::integer as orders,
        coalesce(session_activity.attributed_net_revenue, 0)::numeric(12, 2)
            as attributed_net_revenue,
        coalesce(session_activity.new_customers, 0)::integer as new_customers
    from ad_spend
    left join campaigns
        on ad_spend.campaign_id = campaigns.campaign_id
    left join session_activity
        on
            ad_spend.campaign_id = session_activity.campaign_id
            and ad_spend.spend_date = session_activity.session_date

)

select * from final
