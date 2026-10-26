-- R6: a deletion takes effect at the end of its day, so a deleted customer
-- places no order and causes no event after deleted_at. Events compare
-- occurred_at, not received_at: a late delivery of earlier activity is fine.
with

deleted_customers as (

    select
        customer_id,
        deleted_at
    from {{ ref('stg_shop__customers') }}
    where is_deleted

),

orders_after_deletion as (

    select
        deleted_customers.customer_id,
        deleted_customers.deleted_at,
        'order' as activity,
        orders.order_id::text as activity_id,
        orders.placed_at as activity_at
    from deleted_customers
    inner join {{ ref('stg_shop__orders') }} as orders
        on deleted_customers.customer_id = orders.customer_id
    where orders.placed_at > deleted_customers.deleted_at

),

events_after_deletion as (

    select
        deleted_customers.customer_id,
        deleted_customers.deleted_at,
        'event' as activity,
        events.event_id as activity_id,
        events.occurred_at as activity_at
    from deleted_customers
    inner join {{ ref('stg_events__events') }} as events
        on deleted_customers.customer_id = events.customer_id
    where events.occurred_at > deleted_customers.deleted_at

)

select * from orders_after_deletion
union all
select * from events_after_deletion
