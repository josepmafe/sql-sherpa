-- Nothing was sold on a cancelled order, so its outcome columns are 0.
-- Returns every cancelled order with a non-zero outcome.
select
    order_id,
    net_revenue,
    cogs,
    returned_cogs,
    gross_margin
from {{ ref('fct_orders') }}
where
    is_cancelled
    and (
        net_revenue <> 0
        or cogs <> 0
        or returned_cogs <> 0
        or gross_margin <> 0
    )
