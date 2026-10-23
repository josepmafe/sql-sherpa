with

-- Test accounts (R5) are excluded.
customers as (

    select *
    from {{ ref('stg_shop__customers') }}
    where not is_test_account

),

countries as (

    select * from {{ ref('countries') }}

),

leads as (

    select * from {{ ref('int_identity__lead_to_customer') }}

),

campaigns as (

    select * from {{ ref('stg_marketing__campaigns') }}

),

fct_orders as (

    select * from {{ ref('fct_orders') }}

),

-- Recency is measured against the latest order date, never now(), so the
-- results are deterministic.
as_of as (

    select max(order_date) as as_of_date
    from fct_orders

),

-- Counts and revenue use non-cancelled orders only.
non_cancelled_orders as (

    select *
    from fct_orders
    where not is_cancelled

),

order_aggregates as (

    select
        customer_id,
        min(placed_at) as first_order_at,
        max(placed_at) as last_order_at,
        max(order_date) as last_order_date,
        count(*) as order_count,
        sum(gross_revenue) as lifetime_gross_revenue,
        sum(net_revenue) as lifetime_net_revenue,
        sum(refunded_revenue) as lifetime_refunded_revenue
    from non_cancelled_orders
    group by customer_id

),

ranked_orders as (

    select
        customer_id,
        marketing_channel,
        session_id,
        row_number() over (
            partition by customer_id
            order by placed_at asc, order_id asc
        ) as order_rank
    from non_cancelled_orders

),

first_orders as (

    select
        customer_id,
        marketing_channel,
        session_id
    from ranked_orders
    where order_rank = 1

),

-- Every event of a session carries the session's utm_campaign, so distinct
-- gives one row per session.
first_order_sessions as (

    select distinct
        sessionized.session_id,
        sessionized.session_utm_campaign
    from {{ ref('int_events__sessionized') }} as sessionized
    inner join first_orders
        on sessionized.session_id = first_orders.session_id

),

joined as (

    select
        customers.customer_id,
        customers.country_code,
        countries.name as country_name,
        countries.region,
        customers.signup_at,
        customers.marketing_opt_in,
        customers.is_deleted,
        leads.lead_id,
        leads.lead_source,
        campaigns.campaign_id as acquisition_campaign_id,
        order_aggregates.first_order_at,
        order_aggregates.last_order_at,
        case when customers.is_deleted then null else customers.email end
            as email,
        case when customers.is_deleted then null else customers.first_name end
            as first_name,
        case when customers.is_deleted then null else customers.last_name end
            as last_name,
        coalesce(first_orders.marketing_channel, 'unknown')
            as acquisition_channel,
        coalesce(order_aggregates.order_count, 0) as order_count,
        coalesce(order_aggregates.lifetime_gross_revenue, 0)
            as lifetime_gross_revenue,
        coalesce(order_aggregates.lifetime_net_revenue, 0)
            as lifetime_net_revenue,
        coalesce(order_aggregates.lifetime_refunded_revenue, 0)
            as lifetime_refunded_revenue,
        order_aggregates.lifetime_net_revenue
        / nullif(order_aggregates.order_count, 0) as avg_order_value,
        as_of.as_of_date - order_aggregates.last_order_date
            as days_since_last_order
    from customers
    cross join as_of
    left join countries on customers.country_code = countries.code
    left join leads on customers.customer_id = leads.customer_id
    left join order_aggregates
        on customers.customer_id = order_aggregates.customer_id
    left join first_orders on customers.customer_id = first_orders.customer_id
    left join first_order_sessions
        on first_orders.session_id = first_order_sessions.session_id
    left join campaigns
        on first_order_sessions.session_utm_campaign = campaigns.utm_campaign

),

-- VIPs are the top 10% of buyers by lifetime net revenue, rounded up; ties
-- go to the lowest customer_id.
buyer_ranks as (

    select
        customer_id,
        row_number() over (
            order by lifetime_net_revenue desc, customer_id asc
        ) as revenue_rank,
        count(*) over () as buyer_count
    from joined
    where order_count >= 1

),

final as (

    select
        joined.customer_id::bigint as customer_id,
        joined.email::text as email,
        joined.first_name::text as first_name,
        joined.last_name::text as last_name,
        joined.country_code::text as country_code,
        joined.country_name::text as country_name,
        joined.region::text as region,
        joined.signup_at::timestamptz as signup_at,
        joined.marketing_opt_in::boolean as marketing_opt_in,
        joined.is_deleted::boolean as is_deleted,
        joined.lead_id::bigint as lead_id,
        joined.lead_source::text as lead_source,
        joined.acquisition_channel::text as acquisition_channel,
        joined.acquisition_campaign_id::bigint as acquisition_campaign_id,
        joined.first_order_at::timestamptz as first_order_at,
        joined.last_order_at::timestamptz as last_order_at,
        joined.order_count::integer as order_count,
        joined.lifetime_gross_revenue::numeric(12, 2)
            as lifetime_gross_revenue,
        joined.lifetime_net_revenue::numeric(12, 2) as lifetime_net_revenue,
        joined.lifetime_refunded_revenue::numeric(12, 2)
            as lifetime_refunded_revenue,
        joined.avg_order_value::numeric(12, 2) as avg_order_value,
        joined.days_since_last_order::integer as days_since_last_order,
        (
            {{ customer_segment(
                'joined.order_count', 'joined.days_since_last_order'
            ) }}
        )::text as customer_segment,
        coalesce(
            buyer_ranks.revenue_rank <= ceil(0.10 * buyer_ranks.buyer_count),
            false
        )::boolean as is_vip
    from joined
    left join buyer_ranks on joined.customer_id = buyer_ranks.customer_id

)

select * from final
