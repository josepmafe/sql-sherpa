-- fct_orders numbers each customer's non-cancelled orders the way
-- dim_customers counts them: a customer with orders has exactly one first
-- order, placed at first_order_at, and the highest number is order_count; a
-- customer without orders has no first order. Returns one row per customer
-- breaking this.
with

customer_orders as (

    select
        customer_id,
        count(*) filter (where is_first_order) as first_order_count,
        min(placed_at) filter (where is_first_order) as first_order_at,
        coalesce(max(customer_order_number), 0) as max_order_number
    from {{ ref('fct_orders') }}
    group by customer_id

)

select
    dim_customers.customer_id,
    dim_customers.order_count,
    dim_customers.first_order_at,
    customer_orders.first_order_count,
    customer_orders.first_order_at as fct_first_order_at,
    customer_orders.max_order_number
from {{ ref('dim_customers') }} as dim_customers
left join customer_orders
    on dim_customers.customer_id = customer_orders.customer_id
where
    coalesce(customer_orders.first_order_count, 0)
    <> least(dim_customers.order_count, 1)
    or customer_orders.first_order_at
    is distinct from dim_customers.first_order_at
    or coalesce(customer_orders.max_order_number, 0)
    <> dim_customers.order_count
