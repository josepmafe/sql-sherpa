-- Black Friday is the day after US Thanksgiving, the 4th Thursday of
-- November. The expected Mondays are written out by hand. 2024 is the year
-- the "4th Friday" and "day after the 4th Thursday" definitions differ:
-- 1 November is a Friday, so Thanksgiving is the 28th and the flagged week
-- (25 Nov to 1 Dec) crosses into December. The test returns a row per
-- expected year whose flagged days are not exactly the 7 days starting on
-- the expected Monday.
with

expected as (

    select
        2024 as calendar_year,
        date '2024-11-25' as monday
    union all
    select
        2025 as calendar_year,
        date '2025-11-24' as monday
    union all
    select
        2026 as calendar_year,
        date '2026-11-23' as monday
    union all
    select
        2027 as calendar_year,
        date '2027-11-22' as monday
    union all
    select
        2028 as calendar_year,
        date '2028-11-20' as monday

),

flagged as (

    select
        year as calendar_year,
        min(date_day) as first_flagged_day,
        count(*) as flagged_days
    from {{ ref('dim_date') }}
    where is_black_friday_week
    group by year

)

select
    expected.calendar_year,
    expected.monday,
    flagged.first_flagged_day,
    flagged.flagged_days
from expected
left join flagged on expected.calendar_year = flagged.calendar_year
where
    flagged.first_flagged_day is distinct from expected.monday
    or flagged.flagged_days is distinct from 7
