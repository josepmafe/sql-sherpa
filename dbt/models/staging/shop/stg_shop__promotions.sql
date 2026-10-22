with

source as (

    select * from {{ source('shop', 'promotions') }}

),

renamed as (

    select
        id as promotion_id,
        name as promotion_name,
        type as promotion_type,
        value,
        min_order_value,
        campaign_id,
        starts_at,
        ends_at
    from source

)

select * from renamed
