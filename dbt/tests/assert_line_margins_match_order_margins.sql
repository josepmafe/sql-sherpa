-- Line gross_margin uses the line refunded_revenue, which covers return
-- refunds only and is rounded to cents per line. So the lines of an order add
-- up to the order's gross_margin plus its goodwill refunded revenue, to
-- within a cent per refunded line. Returns one row per order outside that.
with

line_margins as (

    select
        order_id,
        sum(gross_margin) as line_gross_margin,
        count(*) filter (where refunded_revenue > 0) as refunded_line_count
    from {{ ref('fct_order_items') }}
    group by order_id

),

goodwill_refunds as (

    select
        order_id,
        sum(revenue_amount) as goodwill_refunded_revenue
    from {{ ref('fct_refunds') }}
    where refund_reason = 'goodwill'
    group by order_id

),

-- A cancelled order's margin is 0 on the order and on its lines.
expected as (

    select
        fct_orders.order_id,
        fct_orders.gross_margin,
        case
            when fct_orders.is_cancelled then 0
            else coalesce(goodwill_refunds.goodwill_refunded_revenue, 0)
        end as goodwill_refunded_revenue
    from {{ ref('fct_orders') }} as fct_orders
    left join goodwill_refunds
        on fct_orders.order_id = goodwill_refunds.order_id

)

select
    expected.order_id,
    expected.gross_margin,
    expected.goodwill_refunded_revenue,
    line_margins.line_gross_margin,
    line_margins.refunded_line_count
from line_margins
inner join expected on line_margins.order_id = expected.order_id
where
    abs(
        line_margins.line_gross_margin
        - expected.gross_margin
        - expected.goodwill_refunded_revenue
    )
    > 0.01 * line_margins.refunded_line_count
