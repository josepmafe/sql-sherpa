-- The unit cost of each line is the cost the variant had when the order
-- was placed, read from the product variants snapshot. An order placed
-- before the variant's first captured version uses that version's cost.
{{ config(materialized='view') }}

with

order_items as (

    select * from {{ ref('stg_shop__order_items') }}

),

orders as (

    select * from {{ ref('stg_shop__orders') }}

),

versions as (

    select
        id as variant_id,
        cost_price,
        dbt_valid_from,
        dbt_valid_to,
        row_number() over (
            partition by id order by dbt_valid_from
        ) as version_number
    from {{ ref('snap_product_variants') }}

),

earliest_versions as (

    select * from versions
    where version_number = 1

),

lines as (

    select
        order_items.order_item_id,
        order_items.variant_id,
        orders.placed_at
    from order_items
    inner join orders on order_items.order_id = orders.order_id

),

-- The version valid at placed_at: its start is inclusive, its end exclusive.
costed as (

    select
        lines.order_item_id,
        coalesce(valid_at_sale.cost_price, earliest_versions.cost_price)
            as unit_cost_at_sale
    from lines
    left join versions as valid_at_sale
        on
            lines.variant_id = valid_at_sale.variant_id
            and lines.placed_at >= valid_at_sale.dbt_valid_from
            and (
                valid_at_sale.dbt_valid_to is null
                or lines.placed_at < valid_at_sale.dbt_valid_to
            )
    left join earliest_versions
        on lines.variant_id = earliest_versions.variant_id

)

select * from costed
