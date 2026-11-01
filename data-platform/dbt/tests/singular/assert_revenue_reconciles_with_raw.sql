-- Reconciles the sales marts with the raw shop tables, read through
-- source() and not through staging. Per UTC day: the non-cancelled orders
-- of non-test customers (subtotal, discount_total, count) against
-- fct_orders, and their refunds (amount) against fct_refunds. Returns a row
-- per day and measure where the two sides differ.
-- noqa: disable=RF04
with

test_customers as (

    select customers.id as customer_id
    from {{ source('shop', 'customers') }} as customers
    where
        customers.is_test_account
        or {{ normalize_email('customers.email') }} like '%@sqlsherpa-test.com'

),

raw_orders as (

    select
        (orders.placed_at at time zone 'UTC')::date as day,
        sum(orders.subtotal) as gross_revenue,
        sum(orders.discount_total) as discount_amount,
        count(*) as order_count
    from {{ source('shop', 'orders') }} as orders
    where
        orders.status <> 'cancelled'
        and orders.customer_id not in (
            select test_customers.customer_id from test_customers
        )
    group by 1

),

mart_orders as (

    select
        order_date as day,
        sum(gross_revenue) as gross_revenue,
        sum(discount_amount) as discount_amount,
        count(*) as order_count
    from {{ ref('fct_orders') }}
    where not is_cancelled
    group by 1

),

raw_refunds as (

    select
        (refunds.refunded_at at time zone 'UTC')::date as day,
        sum(refunds.amount) as refund_amount
    from {{ source('shop', 'refunds') }} as refunds
    inner join {{ source('shop', 'orders') }} as orders
        on refunds.order_id = orders.id
    where
        orders.customer_id not in (
            select test_customers.customer_id from test_customers
        )
    group by 1

),

mart_refunds as (

    select
        refund_date as day,
        sum(amount) as refund_amount
    from {{ ref('fct_refunds') }}
    group by 1

),

compared as (

    select
        coalesce(raw_orders.day, mart_orders.day) as day,
        'gross_revenue' as measure,
        raw_orders.gross_revenue as raw_value,
        mart_orders.gross_revenue as mart_value
    from raw_orders
    full outer join mart_orders on raw_orders.day = mart_orders.day

    union all

    select
        coalesce(raw_orders.day, mart_orders.day) as day,
        'discount_amount' as measure,
        raw_orders.discount_amount as raw_value,
        mart_orders.discount_amount as mart_value
    from raw_orders
    full outer join mart_orders on raw_orders.day = mart_orders.day

    union all

    select
        coalesce(raw_orders.day, mart_orders.day) as day,
        'order_count' as measure,
        raw_orders.order_count as raw_value,
        mart_orders.order_count as mart_value
    from raw_orders
    full outer join mart_orders on raw_orders.day = mart_orders.day

    union all

    select
        coalesce(raw_refunds.day, mart_refunds.day) as day,
        'refund_amount' as measure,
        raw_refunds.refund_amount as raw_value,
        mart_refunds.refund_amount as mart_value
    from raw_refunds
    full outer join mart_refunds on raw_refunds.day = mart_refunds.day

)

select
    day,
    measure,
    raw_value,
    mart_value
from compared
where coalesce(raw_value, 0) is distinct from coalesce(mart_value, 0)
