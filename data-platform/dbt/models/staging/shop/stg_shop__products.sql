with

source as (

    select * from {{ source('shop', 'products') }}

),

renamed as (

    select
        id as product_id,
        category_id,
        name as product_name,
        brand,
        is_active,
        created_at,
        updated_at
    from source

)

select * from renamed
