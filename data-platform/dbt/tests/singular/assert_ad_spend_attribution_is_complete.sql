-- Every paid campaign's sessions, orders, order revenue and new customers
-- appear in fct_ad_spend_daily once the model has summed them over the
-- campaign's days. The expected values come from fct_sessions, fct_orders and
-- dim_customers.acquisition_campaign_id, not from the model's own
-- is_first_order, so a lost or double-counted day shows up. Spend days cover
-- every day with sessions, so the sums match. Returns one row per campaign
-- and measure that differ.
with

campaigns as (

    select campaign_id
    from {{ ref('dim_campaigns') }}
    where channel <> 'email'

),

modelled as (

    select
        campaign_id,
        sum(sessions) as sessions,
        sum(orders) as orders,
        sum(attributed_net_revenue) as attributed_net_revenue,
        sum(new_customers) as new_customers
    from {{ ref('fct_ad_spend_daily') }}
    group by campaign_id

),

session_totals as (

    select
        sessions.campaign_id,
        count(*) as sessions,
        count(orders.order_id) as orders,
        coalesce(sum(orders.net_revenue), 0) as attributed_net_revenue
    from {{ ref('fct_sessions') }} as sessions
    left join {{ ref('fct_orders') }} as orders
        on
            sessions.session_id = orders.session_id
            and not orders.is_cancelled
    where sessions.campaign_id is not null
    group by sessions.campaign_id

),

acquired as (

    select
        acquisition_campaign_id as campaign_id,
        count(*) as new_customers
    from {{ ref('dim_customers') }}
    where acquisition_campaign_id is not null
    group by acquisition_campaign_id

),

expected as (

    select
        campaigns.campaign_id,
        coalesce(session_totals.sessions, 0) as sessions,
        coalesce(session_totals.orders, 0) as orders,
        coalesce(session_totals.attributed_net_revenue, 0)
            as attributed_net_revenue,
        coalesce(acquired.new_customers, 0) as new_customers
    from campaigns
    left join session_totals
        on campaigns.campaign_id = session_totals.campaign_id
    left join acquired
        on campaigns.campaign_id = acquired.campaign_id

),

comparison as (

    select
        expected.campaign_id,
        measures.measure,
        measures.expected_value,
        measures.modelled_value
    from expected
    left join modelled
        on expected.campaign_id = modelled.campaign_id
    cross join lateral (
        values
        (
            'sessions',
            expected.sessions::numeric,
            coalesce(modelled.sessions, 0)::numeric
        ),
        (
            'orders',
            expected.orders::numeric,
            coalesce(modelled.orders, 0)::numeric
        ),
        (
            'attributed_net_revenue',
            expected.attributed_net_revenue::numeric,
            coalesce(modelled.attributed_net_revenue, 0)::numeric
        ),
        (
            'new_customers',
            expected.new_customers::numeric,
            coalesce(modelled.new_customers, 0)::numeric
        )
    ) as measures (measure, expected_value, modelled_value)

)

select
    campaign_id,
    measure,
    expected_value,
    modelled_value
from comparison
where expected_value <> modelled_value
