-- revenue_excluding_tax removes the tax portion from a tax-inclusive
-- amount (tax_rate 0.20) and rounds to cents. The test returns a row per
-- case whose result differs from the expected value.
with

cases as (

    select
        120.00 as amount,
        100.00 as expected
    union all
    select
        64.80 as amount,
        54.00 as expected
    union all
    select
        1.00 as amount,
        0.83 as expected
    union all
    select
        0.01 as amount,
        0.01 as expected
    union all
    select
        0.00 as amount,
        0.00 as expected

)

select
    amount,
    expected,
    {{ revenue_excluding_tax('amount') }} as actual
from cases
where {{ revenue_excluding_tax('amount') }} is distinct from expected
