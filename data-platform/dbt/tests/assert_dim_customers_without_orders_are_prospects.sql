-- A customer with no non-cancelled order (a prospect, or someone whose only
-- orders were cancelled) has no order dates, recency or average, is a
-- prospect acquired through no channel, and is never a VIP. Returns one row
-- per such customer breaking any of these.
select
    customer_id,
    customer_segment,
    acquisition_channel,
    first_order_at,
    last_order_at,
    days_since_last_order,
    avg_order_value,
    is_vip
from {{ ref('dim_customers') }}
where
    order_count = 0
    and (
        customer_segment <> 'prospect'
        or acquisition_channel <> 'unknown'
        or acquisition_campaign_id is not null
        or first_order_at is not null
        or last_order_at is not null
        or days_since_last_order is not null
        or avg_order_value is not null
        or lifetime_net_revenue <> 0
        or is_vip
    )
