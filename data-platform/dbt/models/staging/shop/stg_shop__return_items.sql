with

source as (

    select * from {{ source('shop', 'return_items') }}

),

renamed as (

    select
        id as return_item_id,
        return_id,
        order_item_id,
        quantity,
        is_restockable
    from source

)

select * from renamed
