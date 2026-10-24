{{ config(materialized='view') }}

with

order_items as (

    select * from {{ ref('stg_shop__order_items') }}

),

order_discounts as (

    select * from {{ ref('stg_shop__order_discounts') }}

),

lines as (

    select
        order_item_id,
        order_id,
        variant_id,
        quantity,
        unit_price,
        quantity * unit_price as gross_amount
    from order_items

),

discount_per_order as (

    select
        order_id,
        sum(amount) as order_discount
    from order_discounts
    group by order_id

),

shares as (

    select
        lines.*,
        coalesce(discount_per_order.order_discount, 0) as order_discount,
        sum(lines.gross_amount) over (
            partition by lines.order_id
        ) as order_gross_amount,
        row_number() over (
            partition by lines.order_id
            order by lines.gross_amount desc, lines.order_item_id asc
        ) as size_rank
    from lines
    left join discount_per_order
        on lines.order_id = discount_per_order.order_id

),

rounded_shares as (

    select
        *,
        coalesce(
            round(
                order_discount * gross_amount
                / nullif(order_gross_amount, 0),
                2
            ),
            0
        ) as rounded_share
    from shares

),

rounded_totals as (

    select
        *,
        sum(rounded_share) over (
            partition by order_id
        ) as order_rounded_total
    from rounded_shares

),

-- Rounding each share can leave the lines a few cents off the order
-- discount; the largest line absorbs the difference.
allocated as (

    select
        *,
        rounded_share + case
            when size_rank = 1
                then order_discount - order_rounded_total
            else 0
        end as allocated_discount
    from rounded_totals

),

final as (

    select
        order_item_id,
        order_id,
        variant_id,
        quantity,
        unit_price,
        gross_amount,
        allocated_discount,
        gross_amount - allocated_discount as net_amount
    from allocated

)

select * from final
