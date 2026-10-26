-- Returns each received_at date (UTC) with the number of source events
-- missing from stg_events__events; dbt fails the test if any row comes back.
-- Cause: microbatch picks the batches of an incremental run from the wall clock
-- (today and the 7 days before), not from the data. After advancing the
-- simulation by more days than that, the oldest days are skipped.
-- Fix: load the missing days, then run the normal build:
--   dbt build --select stg_events__events
--     --event-time-start <first day> --event-time-end <last day + 1>
-- Because this test fails the build before int_events__sessionized runs, the
-- sessions' watermark does not move past the missing events.
select
    (source_events.received_at at time zone 'UTC')::date as received_date,
    count(distinct source_events.event_id) as missing_events
from {{ source('events', 'events') }} as source_events
left join {{ ref('stg_events__events') }} as staged
    on source_events.event_id = staged.event_id
where staged.event_id is null
group by 1
