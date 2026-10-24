with

source as (

    select * from {{ source('shop', 'order_discounts') }}

),

renamed as (

    select
        id as order_discount_id,
        order_id,
        promotion_id,
        coupon_code_id,
        amount
    from source

)

select * from renamed
