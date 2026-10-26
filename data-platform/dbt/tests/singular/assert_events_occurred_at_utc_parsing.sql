-- A singular test is a SELECT in tests/; dbt fails it if it returns any rows.
-- Branch 1 checks that casting offset text to timestamptz gives UTC: all three
-- literals are 09:00 UTC on their dates (+01:00 winter, +02:00 summer, UTC).
-- Branch 2 checks the model's real rows against the raw text, with the UTC
-- instant computed independently (local date-time minus the explicit offset)
-- instead of reusing the model's cast.
with

cases as (

    select
        '2025-01-15T10:00:00+01:00' as raw_value,
        timestamptz '2025-01-15 09:00:00+00' as expected_utc
    union all
    select
        '2025-07-15T11:00:00+02:00' as raw_value,
        timestamptz '2025-07-15 09:00:00+00' as expected_utc
    union all
    select
        '2025-07-15T09:00:00+00:00' as raw_value,
        timestamptz '2025-07-15 09:00:00+00' as expected_utc

),

literal_mismatches as (

    select
        raw_value,
        expected_utc,
        raw_value::timestamptz as parsed_utc
    from cases
    where raw_value::timestamptz is distinct from expected_utc

),

raw_events as (

    select
        event_id,
        occurred_at as raw_value,
        left(occurred_at, 19)::timestamp as local_time,
        case
            when right(occurred_at, 1) = 'Z' then interval '0'
            when substr(right(occurred_at, 6), 1, 1) = '-'
                then -1 * right(occurred_at, 5)::interval
            else right(occurred_at, 5)::interval
        end as utc_offset
    from {{ source('events', 'events') }}

),

model_mismatches as (

    select
        raw_events.raw_value,
        (raw_events.local_time - raw_events.utc_offset) at time zone 'UTC'
            as expected_utc,
        stg.occurred_at as parsed_utc
    from raw_events
    inner join {{ ref('stg_events__events') }} as stg
        on raw_events.event_id = stg.event_id
    where
        date_trunc('second', stg.occurred_at) is distinct from (
            (raw_events.local_time - raw_events.utc_offset) at time zone 'UTC'
        )

)

select * from literal_mismatches
union all
select * from model_mismatches
