with

source as (

    select * from {{ source('shop', 'coupon_codes') }}

),

renamed as (

    select
        id as coupon_code_id,
        promotion_id,
        code,
        max_redemptions,
        is_single_use
    from source

)

select * from renamed
