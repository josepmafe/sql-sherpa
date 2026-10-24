{{
    config(
        indexes=[
            {'columns': ['order_id']},
            {'columns': ['variant_id']},
        ]
    )
}}

with

-- fct_orders already excludes the orders of test accounts (R5); the inner
-- join drops their lines.
orders as (

    select * from {{ ref('fct_orders') }}

),

lines as (

    select * from {{ ref('int_order_items__discount_allocated') }}

),

costed as (

    select * from {{ ref('int_order_items__costed') }}

),

returns_summarized as (

    select * from {{ ref('int_order_items__returns_summarized') }}

),

products as (

    select * from {{ ref('dim_products') }}

),

joined as (

    select
        lines.order_item_id,
        lines.order_id,
        orders.customer_id,
        lines.variant_id,
        products.product_id,
        orders.order_date,
        orders.is_cancelled,
        lines.quantity,
        lines.unit_price,
        lines.gross_amount,
        lines.allocated_discount,
        lines.net_amount,
        costed.unit_cost_at_sale,
        coalesce(returns_summarized.returned_quantity, 0) as returned_quantity,
        coalesce(returns_summarized.restockable_returned_quantity, 0)
            as restockable_returned_quantity,
        coalesce(returns_summarized.refunded_revenue, 0) as refunded_revenue
    from lines
    inner join orders on lines.order_id = orders.order_id
    inner join costed on lines.order_item_id = costed.order_item_id
    left join products on lines.variant_id = products.variant_id
    left join returns_summarized
        on lines.order_item_id = returns_summarized.order_item_id

),

-- Nothing was sold on a cancelled order, so its lines' outcomes are 0.
outcomes as (

    select
        *,
        case
            when is_cancelled then 0 else quantity * unit_cost_at_sale
        end as cogs,
        case
            when is_cancelled then 0
            else restockable_returned_quantity * unit_cost_at_sale
        end as returned_cogs
    from joined

),

-- Same definition as fct_orders.gross_margin, with the line's net amount and
-- refunded revenue.
margins as (

    select
        *,
        case
            when is_cancelled then 0
            else net_amount - refunded_revenue - (cogs - returned_cogs)
        end as gross_margin
    from outcomes

),

final as (

    select
        order_item_id::bigint as order_item_id,
        order_id::bigint as order_id,
        customer_id::bigint as customer_id,
        variant_id::bigint as variant_id,
        product_id::bigint as product_id,
        order_date::date as order_date,
        is_cancelled::boolean as is_cancelled,
        quantity::integer as quantity,
        unit_price::numeric(12, 2) as unit_price,
        gross_amount::numeric(12, 2) as gross_amount,
        allocated_discount::numeric(12, 2) as allocated_discount,
        net_amount::numeric(12, 2) as net_amount,
        unit_cost_at_sale::numeric(12, 2) as unit_cost_at_sale,
        cogs::numeric(12, 2) as cogs,
        returned_quantity::integer as returned_quantity,
        restockable_returned_quantity::integer
            as restockable_returned_quantity,
        refunded_revenue::numeric(12, 2) as refunded_revenue,
        returned_cogs::numeric(12, 2) as returned_cogs,
        gross_margin::numeric(12, 2) as gross_margin
    from margins

)

select * from final
