{{
    config(
        indexes=[
            {'columns': ['order_date']},
            {'columns': ['customer_id']},
        ]
    )
}}

with

customers as (

    select * from {{ ref('stg_shop__customers') }}

),

-- Orders of test accounts (R5) are excluded.
orders as (

    select orders.*
    from {{ ref('stg_shop__orders') }} as orders
    inner join customers on orders.customer_id = customers.customer_id
    where not customers.is_test_account

),

addresses as (

    select * from {{ ref('stg_shop__addresses') }}

),

status_pivoted as (

    select * from {{ ref('int_orders__status_pivoted') }}

),

payments as (

    select * from {{ ref('int_orders__payments_summarized') }}

),

refunds as (

    select * from {{ ref('int_orders__refunds_summarized') }}

),

-- Each purchase event carries the order it created; its session gives the
-- order's channel.
purchase_sessions as (

    select
        order_id,
        session_id,
        marketing_channel
    from {{ ref('int_events__sessionized') }}
    where event_type = 'purchase' and order_id is not null

),

lines as (

    select
        discount_allocated.order_id,
        sum(discount_allocated.quantity) as item_count,
        sum(discount_allocated.gross_amount) as gross_revenue,
        sum(discount_allocated.allocated_discount) as discount_amount,
        sum(discount_allocated.quantity * costed.unit_cost_at_sale) as cogs,
        coalesce(
            sum(
                returns_summarized.restockable_returned_quantity
                * costed.unit_cost_at_sale
            ),
            0
        ) as returned_cogs
    from {{ ref('int_order_items__discount_allocated') }} as discount_allocated
    inner join {{ ref('int_order_items__costed') }} as costed
        on discount_allocated.order_item_id = costed.order_item_id
    left join
        {{ ref('int_order_items__returns_summarized') }} as returns_summarized
        on discount_allocated.order_item_id = returns_summarized.order_item_id
    group by discount_allocated.order_id

),

-- Free-shipping discounts have amount 0 and do not make an order discounted.
discounted_orders as (

    select distinct order_id
    from {{ ref('stg_shop__order_discounts') }}
    where amount > 0

),

returned_orders as (

    select distinct order_id
    from {{ ref('stg_shop__returns') }}
    where return_status <> 'rejected'

),

joined as (

    select
        orders.order_id,
        orders.order_number,
        orders.customer_id,
        orders.placed_at,
        orders.order_status,
        orders.sales_channel,
        orders.shipping_amount,
        orders.tax_amount,
        orders.order_total,
        status_pivoted.paid_at,
        status_pivoted.shipped_at,
        status_pivoted.delivered_at,
        status_pivoted.cancelled_at,
        purchase_sessions.session_id,
        addresses.country_code as ship_country_code,
        lines.item_count,
        lines.gross_revenue,
        lines.discount_amount,
        lines.cogs,
        lines.returned_cogs,
        (orders.placed_at at time zone 'UTC')::date as order_date,
        orders.order_status = 'cancelled' as is_cancelled,
        -- An app order has no session (R12). A web order whose purchase
        -- event has not arrived yet (R2) has none either, until it lands.
        case
            when orders.sales_channel = 'app' then 'app'
            else coalesce(purchase_sessions.marketing_channel, 'unknown')
        end as marketing_channel,
        coalesce(payments.captured_amount, 0) as captured_amount,
        coalesce(refunds.refunded_amount, 0) as refunded_amount,
        coalesce(refunds.refunded_revenue, 0) as refunded_revenue,
        discounted_orders.order_id is not null as has_discount,
        returned_orders.order_id is not null as has_return,
        (status_pivoted.delivered_at at time zone 'UTC')::date
        - (orders.placed_at at time zone 'UTC')::date as days_to_deliver
    from orders
    left join status_pivoted on orders.order_id = status_pivoted.order_id
    left join payments on orders.order_id = payments.order_id
    left join refunds on orders.order_id = refunds.order_id
    left join purchase_sessions
        on
            orders.order_id = purchase_sessions.order_id
            and orders.sales_channel = 'web'
    left join addresses
        on orders.shipping_address_id = addresses.address_id
    left join lines on orders.order_id = lines.order_id
    left join discounted_orders
        on orders.order_id = discounted_orders.order_id
    left join returned_orders on orders.order_id = returned_orders.order_id

),

-- A cancelled order keeps its amounts as placed; its outcomes are 0
-- because nothing was sold.
outcomes as (

    select
        *,
        case
            when is_cancelled then 0
            else gross_revenue - discount_amount - refunded_revenue
        end as net_revenue,
        case when is_cancelled then 0 else cogs end as outcome_cogs,
        case
            when is_cancelled then 0 else returned_cogs
        end as outcome_returned_cogs
    from joined

),

-- All three outcomes are 0 on a cancelled order, so the margin is too.
margins as (

    select
        *,
        net_revenue - (outcome_cogs - outcome_returned_cogs) as gross_margin
    from outcomes

),

final as (

    select
        order_id::bigint as order_id,
        order_number::text as order_number,
        customer_id::bigint as customer_id,
        order_date::date as order_date,
        placed_at::timestamptz as placed_at,
        paid_at::timestamptz as paid_at,
        shipped_at::timestamptz as shipped_at,
        delivered_at::timestamptz as delivered_at,
        cancelled_at::timestamptz as cancelled_at,
        order_status::text as current_status,
        is_cancelled::boolean as is_cancelled,
        sales_channel::text as sales_channel,
        marketing_channel::text as marketing_channel,
        session_id::text as session_id,
        ship_country_code::text as ship_country_code,
        item_count::integer as item_count,
        gross_revenue::numeric(12, 2) as gross_revenue,
        discount_amount::numeric(12, 2) as discount_amount,
        shipping_amount::numeric(12, 2) as shipping_amount,
        tax_amount::numeric(12, 2) as tax_amount,
        order_total::numeric(12, 2) as order_total,
        captured_amount::numeric(12, 2) as captured_amount,
        refunded_amount::numeric(12, 2) as refunded_amount,
        refunded_revenue::numeric(12, 2) as refunded_revenue,
        net_revenue::numeric(12, 2) as net_revenue,
        outcome_cogs::numeric(12, 2) as cogs,
        outcome_returned_cogs::numeric(12, 2) as returned_cogs,
        gross_margin::numeric(12, 2) as gross_margin,
        has_discount::boolean as has_discount,
        has_return::boolean as has_return,
        days_to_deliver::integer as days_to_deliver
    from margins

)

select * from final
