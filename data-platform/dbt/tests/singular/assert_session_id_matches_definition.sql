-- Pins down the session_id definition (spec 6.4): md5 of anonymous_id and
-- the epoch microseconds of session_start_at, which does not depend on the
-- connection's time zone the way a timestamp-to-text cast does.
select
    event_id,
    session_id
from {{ ref('int_events__sessionized') }}
where
    session_id <> md5(
        anonymous_id
        || '|'
        || (extract(epoch from session_start_at) * 1000000)::bigint::text
    )
