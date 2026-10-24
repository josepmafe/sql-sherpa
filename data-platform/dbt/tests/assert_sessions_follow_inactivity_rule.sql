-- A session boundary must fall exactly where the gap to the previous event
-- of the same anonymous_id exceeds the timeout. Returns events where the gap
-- is within the timeout but the session changed, or beyond it but the
-- session stayed the same.
with

ordered as (

    select
        event_id,
        anonymous_id,
        occurred_at,
        session_id,
        lag(occurred_at) over (
            partition by anonymous_id order by occurred_at asc, event_id asc
        ) as previous_occurred_at,
        lag(session_id) over (
            partition by anonymous_id order by occurred_at asc, event_id asc
        ) as previous_session_id
    from {{ ref('int_events__sessionized') }}

)

select *
from ordered
where
    previous_occurred_at is not null
    and (
        (
            occurred_at - previous_occurred_at
            <= make_interval(mins => {{ var('session_timeout_minutes') }})
            and session_id <> previous_session_id
        )
        or (
            occurred_at - previous_occurred_at
            > make_interval(mins => {{ var('session_timeout_minutes') }})
            and session_id = previous_session_id
        )
    )
