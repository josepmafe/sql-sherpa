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

final as (

    select
        lines.order_item_id::bigint as order_item_id,
        lines.order_id::bigint as order_id,
        orders.customer_id::bigint as customer_id,
        lines.variant_id::bigint as variant_id,
        products.product_id::bigint as product_id,
        orders.order_date::date as order_date,
        orders.is_cancelled::boolean as is_cancelled,
        lines.quantity::integer as quantity,
        lines.unit_price::numeric(12, 2) as unit_price,
        lines.gross_amount::numeric(12, 2) as gross_amount,
        lines.allocated_discount::numeric(12, 2) as allocated_discount,
        lines.net_amount::numeric(12, 2) as net_amount,
        costed.unit_cost_at_sale::numeric(12, 2) as unit_cost_at_sale,
        -- Nothing was sold on a cancelled order, so its lines cost nothing.
        (
            case
                when orders.is_cancelled then 0
                else lines.quantity * costed.unit_cost_at_sale
            end
        )::numeric(12, 2) as cogs,
        coalesce(returns_summarized.returned_quantity, 0)::integer
            as returned_quantity,
        coalesce(returns_summarized.refunded_revenue, 0)::numeric(12, 2)
            as refunded_revenue
    from lines
    inner join orders on lines.order_id = orders.order_id
    inner join costed on lines.order_item_id = costed.order_item_id
    left join products on lines.variant_id = products.variant_id
    left join returns_summarized
        on lines.order_item_id = returns_summarized.order_item_id

)

select * from final
