{{
    config(
        indexes=[
            {'columns': ['order_id']},
            {'columns': ['promotion_id']},
        ]
    )
}}

with

order_discounts as (

    select * from {{ ref('stg_shop__order_discounts') }}

),

orders as (

    select
        order_id,
        customer_id,
        order_date,
        is_cancelled
    from {{ ref('fct_orders') }}

),

promotions as (

    select * from {{ ref('stg_shop__promotions') }}

),

coupon_codes as (

    select * from {{ ref('stg_shop__coupon_codes') }}

),

-- Inner join: discounts on orders of test accounts drop out (R5).
joined as (

    select
        order_discounts.order_discount_id,
        order_discounts.order_id,
        orders.customer_id,
        orders.order_date,
        order_discounts.promotion_id,
        coupon_codes.code as coupon_code,
        promotions.promotion_type,
        order_discounts.amount,
        orders.is_cancelled
    from order_discounts
    inner join orders on order_discounts.order_id = orders.order_id
    inner join promotions
        on order_discounts.promotion_id = promotions.promotion_id
    left join coupon_codes
        on order_discounts.coupon_code_id = coupon_codes.coupon_code_id

),

final as (

    select
        order_discount_id::bigint as order_discount_id,
        order_id::bigint as order_id,
        customer_id::bigint as customer_id,
        order_date::date as order_date,
        promotion_id::bigint as promotion_id,
        coupon_code::text as coupon_code,
        promotion_type::text as promotion_type,
        amount::numeric(12, 2) as amount,
        is_cancelled::boolean as is_cancelled
    from joined

)

select * from final
