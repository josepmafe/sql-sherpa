-- A web order and its session agree. A session holds one order_id, its first
-- purchase's, so a later order of the same session (the sessionizer merges
-- purchases within the 30-minute gap) only needs its purchase event to be in
-- that session and the session to have purchased. Returns one row per order
-- whose session_id is not the session of its purchase event, or is missing
-- from fct_sessions or did not purchase, and per session whose order_id points
-- to an order with another session_id.
select
    fct_orders.order_id,
    fct_orders.session_id,
    'order is not in a purchased session holding its purchase event' as problem
from {{ ref('fct_orders') }}
left join {{ ref('fct_sessions') }}
    on fct_orders.session_id = fct_sessions.session_id
where
    fct_orders.session_id is not null
    and (
        not coalesce(fct_sessions.purchased, false)
        or not exists (
            select 1
            from {{ ref('int_events__sessionized') }} as purchases
            where
                purchases.event_type = 'purchase'
                and purchases.order_id = fct_orders.order_id
                and purchases.session_id = fct_orders.session_id
        )
    )

union all

select
    fct_sessions.order_id,
    fct_sessions.session_id,
    'session order is not in the session' as problem
from {{ ref('fct_sessions') }}
left join {{ ref('fct_orders') }}
    on fct_sessions.order_id = fct_orders.order_id
where
    fct_sessions.order_id is not null
    and fct_orders.session_id is distinct from fct_sessions.session_id
