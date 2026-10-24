{{ config(indexes=[{'columns': ['order_id']}]) }}

with

returns as (

    select * from {{ ref('stg_shop__returns') }}

),

orders as (

    select
        order_id,
        customer_id
    from {{ ref('fct_orders') }}

),

return_items as (

    select
        return_id,
        sum(quantity) as returned_item_quantity,
        coalesce(
            sum(quantity) filter (where is_restockable), 0
        ) as restockable_quantity
    from {{ ref('stg_shop__return_items') }}
    group by return_id

),

refunds as (

    select
        return_id,
        refund_id,
        amount
    from {{ ref('stg_shop__refunds') }}
    where return_id is not null

),

-- Inner join: returns on orders of test accounts drop out (R5).
joined as (

    select
        returns.return_id,
        returns.order_id,
        orders.customer_id,
        returns.return_reason,
        returns.return_status,
        returns.requested_at,
        refunds.refund_id,
        refunds.amount,
        coalesce(return_items.returned_item_quantity, 0)
            as returned_item_quantity,
        coalesce(return_items.restockable_quantity, 0)
            as restockable_quantity
    from returns
    inner join orders on returns.order_id = orders.order_id
    left join return_items on returns.return_id = return_items.return_id
    left join refunds on returns.return_id = refunds.return_id

),

final as (

    select
        return_id::bigint as return_id,
        order_id::bigint as order_id,
        customer_id::bigint as customer_id,
        return_reason::text as reason,
        return_status::text as status,
        requested_at::timestamptz as requested_at,
        returned_item_quantity::integer as returned_item_quantity,
        restockable_quantity::integer as restockable_quantity,
        refund_id::bigint as refund_id,
        amount::numeric(12, 2) as refunded_amount
    from joined

)

select * from final
