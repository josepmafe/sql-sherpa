-- data-platform-3 switches unit_cost_at_sale to the product variants
-- snapshot, matched on the order's placed_at.
with

order_items as (

    select * from {{ ref('stg_shop__order_items') }}

),

product_variants as (

    select * from {{ ref('stg_shop__product_variants') }}

),

costed as (

    select
        order_items.order_item_id,
        product_variants.cost_price as unit_cost_at_sale
    from order_items
    inner join product_variants
        on order_items.variant_id = product_variants.variant_id

)

select * from costed
