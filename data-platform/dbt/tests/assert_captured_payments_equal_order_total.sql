-- A paid order that is not cancelled has captured exactly its order_total
-- (failed attempts are retried, split payments add up). Returns every such
-- order whose captured payments differ.
select
    order_id,
    order_total,
    captured_amount
from {{ ref('fct_orders') }}
where
    not is_cancelled
    and paid_at is not null
    and captured_amount <> order_total
