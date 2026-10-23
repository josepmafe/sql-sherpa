{#-
    A date spine is a generated table with one row per calendar day, so every day exists
    even when nothing happened on it. The range comes from static vars, never from the
    data, so the model compiles the same way on an empty database. date_spine's end_date
    is exclusive, hence + 1.
-#}

with

spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="'" ~ var('date_spine_start') ~ "'::date",
        end_date="'" ~ var('date_spine_end') ~ "'::date + 1"
    ) }}

),

days as (

    select
        date_day::date as date_day,
        make_date(extract(year from date_day)::int, 11, 1) as november_1st
    from spine

),

with_black_friday as (

    -- Black Friday is the 4th Friday of November:
    -- the first Friday on or after 1 November, plus 3 weeks.
    select
        date_day,
        november_1st
        + (5 - extract(isodow from november_1st)::int + 7) % 7
        + 21 as black_friday
    from days

),

final as (

    select
        date_day,
        -- Postgres weeks start on Monday.
        date_trunc('week', date_day)::date as week_start,
        date_trunc('month', date_day)::date as month_start,
        -- The spec names these columns; quarter and year are SQL keywords.
        extract(quarter from date_day)::int as quarter,  -- noqa: RF04
        extract(year from date_day)::int as year,  -- noqa: RF04
        to_char(date_day, 'FMDay') as day_of_week_name,
        extract(isodow from date_day) in (6, 7) as is_weekend,
        date_trunc('week', date_day)
        = date_trunc('week', black_friday) as is_black_friday_week
    from with_black_friday

)

select * from final
