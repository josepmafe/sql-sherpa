-- Line refunded_revenue covers return refunds only, so the lines add up to at
-- most the order's refunded_revenue. Each line is rounded to cents on its
-- own, so the lines may exceed it by up to a cent per refunded line.
with

line_refunds as (

    select
        order_id,
        sum(refunded_revenue) as line_refunded_revenue,
        count(*) filter (where refunded_revenue > 0) as refunded_line_count
    from {{ ref('fct_order_items') }}
    group by order_id

)

select
    fct_orders.order_id,
    fct_orders.refunded_revenue,
    line_refunds.line_refunded_revenue,
    line_refunds.refunded_line_count
from line_refunds
inner join {{ ref('fct_orders') }} as fct_orders
    on line_refunds.order_id = fct_orders.order_id
where
    line_refunds.line_refunded_revenue
    > fct_orders.refunded_revenue + 0.01 * line_refunds.refunded_line_count
