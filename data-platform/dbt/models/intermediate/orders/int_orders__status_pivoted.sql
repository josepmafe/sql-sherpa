with

status_history as (

    select * from {{ ref('stg_shop__order_status_history') }}

),

pivoted as (

    select
        order_id,
        min(changed_at) filter (where status = 'paid') as paid_at,
        min(changed_at) filter (where status = 'shipped') as shipped_at,
        min(changed_at) filter (where status = 'delivered') as delivered_at,
        min(changed_at) filter (where status = 'cancelled') as cancelled_at
    from status_history
    group by order_id

)

select * from pivoted
