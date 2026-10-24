with

source as (

    select * from {{ source('shop', 'product_variants') }}

),

renamed as (

    select
        id as variant_id,
        product_id,
        sku,
        size,
        color,
        cost_price,
        list_price,
        created_at,
        updated_at
    from source

)

select * from renamed
