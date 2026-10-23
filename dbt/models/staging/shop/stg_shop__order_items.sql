with

source as (

    select * from {{ source('shop', 'order_items') }}

),

renamed as (

    select
        id as order_item_id,
        order_id,
        variant_id,
        quantity,
        unit_price
    from source

)

select * from renamed
