-- Test accounts (R5) and their orders are excluded from every mart. Returns
-- one row per test-account customer found in dim_customers, fct_orders or
-- fct_sessions.
with

test_accounts as (

    select customer_id
    from {{ ref('stg_shop__customers') }}
    where is_test_account

),

mart_customers as (

    select
        customer_id,
        'dim_customers' as mart
    from {{ ref('dim_customers') }}
    union all
    select distinct
        customer_id,
        'fct_orders' as mart
    from {{ ref('fct_orders') }}
    union all
    select distinct
        customer_id,
        'fct_sessions' as mart
    from {{ ref('fct_sessions') }}

)

select
    mart_customers.customer_id,
    mart_customers.mart
from mart_customers
inner join test_accounts
    on mart_customers.customer_id = test_accounts.customer_id
