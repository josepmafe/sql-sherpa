-- A singular test is a SELECT in tests/; dbt fails it if it returns any rows.
-- It checks that casting the tracker's offset text to timestamptz gives UTC:
-- all three literals are 09:00 UTC on their dates (+01:00 winter, +02:00
-- summer, already UTC).
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

parsed as (

    select
        raw_value,
        expected_utc,
        raw_value::timestamptz as parsed_utc
    from cases

)

select * from parsed
where parsed_utc is distinct from expected_utc
