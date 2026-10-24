with

source as (

    select * from {{ source('shop', 'categories') }}

),

renamed as (

    select
        id as category_id,
        parent_id as parent_category_id,
        name as category_name
    from source

)

select * from renamed
