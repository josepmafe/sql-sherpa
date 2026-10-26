-- A web order and the session of its purchase event agree. Returns one row
-- per order whose session is missing from fct_sessions or did not purchase,
-- and per session whose order is missing or has another session_id. A session
-- holds one order_id, its first purchase's, so a later order of the same
-- session only needs the session to have purchased.
select
    fct_orders.order_id,
    fct_orders.session_id,
    'order has no matching session' as problem
from {{ ref('fct_orders') }}
left join {{ ref('fct_sessions') }}
    on fct_orders.session_id = fct_sessions.session_id
where
    fct_orders.session_id is not null
    and fct_sessions.order_id is null

union all

select
    fct_sessions.order_id,
    fct_sessions.session_id,
    'session has no matching order' as problem
from {{ ref('fct_sessions') }}
left join {{ ref('fct_orders') }}
    on fct_sessions.order_id = fct_orders.order_id
where
    fct_sessions.order_id is not null
    and fct_orders.session_id is distinct from fct_sessions.session_id
