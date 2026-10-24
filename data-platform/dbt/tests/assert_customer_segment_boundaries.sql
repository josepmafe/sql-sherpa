-- customer_segment at the 90/91 and 180/181 day boundaries and the
-- order-count edges. Returns a row per case whose segment differs from the
-- expected one.
with

cases as (

    select
        0 as order_count,
        null::integer as days_since_last_order,
        'prospect' as expected
    union all
    select
        1 as order_count,
        0 as days_since_last_order,
        'new' as expected
    union all
    select
        1 as order_count,
        90 as days_since_last_order,
        'new' as expected
    union all
    select
        2 as order_count,
        90 as days_since_last_order,
        'active' as expected
    union all
    select
        1 as order_count,
        91 as days_since_last_order,
        'at_risk' as expected
    union all
    select
        5 as order_count,
        180 as days_since_last_order,
        'at_risk' as expected
    union all
    select
        1 as order_count,
        181 as days_since_last_order,
        'churned' as expected

)

select
    order_count,
    days_since_last_order,
    expected,
    {{ customer_segment('order_count', 'days_since_last_order') }} as actual
from cases
where
    {{ customer_segment('order_count', 'days_since_last_order') }}
    is distinct from expected
