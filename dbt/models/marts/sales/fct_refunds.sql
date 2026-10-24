{{ config(indexes=[{'columns': ['order_id']}]) }}

with

refunds as (

    select * from {{ ref('stg_shop__refunds') }}

),

orders as (

    select
        order_id,
        customer_id,
        order_date
    from {{ ref('fct_orders') }}

),

-- Inner join: refunds on orders of test accounts drop out (R5).
joined as (

    select
        refunds.refund_id,
        refunds.order_id,
        orders.customer_id,
        refunds.return_id,
        refunds.refund_reason,
        refunds.refunded_at,
        refunds.amount,
        (refunds.refunded_at at time zone 'UTC')::date as refund_date,
        case
            when refunds.refund_reason = 'cancellation' then 0
            else {{ revenue_excluding_tax('refunds.amount') }}
        end as revenue_amount,
        (refunds.refunded_at at time zone 'UTC')::date
        - orders.order_date as days_since_order
    from refunds
    inner join orders on refunds.order_id = orders.order_id

),

final as (

    select
        refund_id::bigint as refund_id,
        order_id::bigint as order_id,
        customer_id::bigint as customer_id,
        return_id::bigint as return_id,
        refund_reason::text as refund_reason,
        refunded_at::timestamptz as refunded_at,
        refund_date::date as refund_date,
        amount::numeric(12, 2) as amount,
        revenue_amount::numeric(12, 2) as revenue_amount,
        days_since_order::integer as days_since_order
    from joined

)

select * from final
