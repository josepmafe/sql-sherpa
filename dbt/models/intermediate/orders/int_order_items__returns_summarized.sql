{{ config(materialized='view') }}

with

returns as (

    select * from {{ ref('stg_shop__returns') }}

),

return_items as (

    select * from {{ ref('stg_shop__return_items') }}

),

discount_allocated as (

    select * from {{ ref('int_order_items__discount_allocated') }}

),

received_quantities as (

    select
        return_items.order_item_id,
        sum(return_items.quantity) as returned_quantity,
        coalesce(
            sum(return_items.quantity) filter (
                where return_items.is_restockable
            ),
            0
        ) as restockable_returned_quantity
    from return_items
    inner join returns on return_items.return_id = returns.return_id
    where returns.return_status = 'received'
    group by return_items.order_item_id

),

summarized as (

    select
        received_quantities.order_item_id,
        received_quantities.returned_quantity,
        received_quantities.restockable_returned_quantity,
        round(
            received_quantities.returned_quantity
            * discount_allocated.net_amount
            / discount_allocated.quantity,
            2
        ) as refunded_revenue
    from received_quantities
    inner join discount_allocated
        on received_quantities.order_item_id = discount_allocated.order_item_id

)

select * from summarized
