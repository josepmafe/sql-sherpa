-- fct_orders keeps every order of a non-test customer, including web orders
-- whose purchase event has not arrived (R2): those have no session and
-- marketing_channel unknown. Returns a row when the order counts differ, and
-- one per web order without a session whose channel is not unknown.
with

fct_orders as (

    select * from {{ ref('fct_orders') }}

),

non_test_orders as (

    select orders.order_id
    from {{ ref('stg_shop__orders') }} as orders
    inner join {{ ref('stg_shop__customers') }} as customers
        on orders.customer_id = customers.customer_id
    where not customers.is_test_account

),

counts as (

    select
        (select count(*) from fct_orders) as fct_order_count,
        (select count(*) from non_test_orders) as non_test_order_count

),

count_mismatch as (

    select
        null::bigint as order_id,
        'fct_orders has ' || fct_order_count || ' orders, staging has '
        || non_test_order_count as problem
    from counts
    where fct_order_count <> non_test_order_count

),

sessionless_web_orders as (

    select
        order_id,
        'web order without a session has channel '
        || marketing_channel as problem
    from fct_orders
    where
        sales_channel = 'web'
        and session_id is null
        and marketing_channel <> 'unknown'

)

select * from count_mismatch
union all
select * from sessionless_web_orders
