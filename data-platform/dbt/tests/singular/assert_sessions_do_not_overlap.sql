-- Sessions of one anonymous_id must be disjoint in time: the range from a
-- session's first to its last event may not intersect another session's.
with

session_ranges as (

    select
        anonymous_id,
        session_id,
        min(occurred_at) as first_event_at,
        max(occurred_at) as last_event_at
    from {{ ref('int_events__sessionized') }}
    group by anonymous_id, session_id

)

select
    earlier.anonymous_id,
    earlier.session_id,
    later.session_id as overlapping_session_id
from session_ranges as earlier
inner join session_ranges as later
    on
        earlier.anonymous_id = later.anonymous_id
        and earlier.session_id < later.session_id
        and earlier.first_event_at <= later.last_event_at
        and earlier.last_event_at >= later.first_event_at
