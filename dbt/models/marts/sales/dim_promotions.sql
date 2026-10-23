with

promotions as (

    select * from {{ ref('stg_shop__promotions') }}

),

coupon_codes as (

    select
        promotion_id,
        count(*) as coupon_code_count
    from {{ ref('stg_shop__coupon_codes') }}
    group by promotion_id

),

customers as (

    select * from {{ ref('stg_shop__customers') }}

),

-- Orders of test accounts (R5) never count. Cancelled orders do: the
-- redemption happened.
non_test_orders as (

    select orders.order_id
    from {{ ref('stg_shop__orders') }} as orders
    left join customers on orders.customer_id = customers.customer_id
    where not coalesce(customers.is_test_account, false)

),

redemptions as (

    select
        order_discounts.promotion_id,
        count(*) as redemption_count,
        sum(order_discounts.amount) as total_discount_amount
    from {{ ref('stg_shop__order_discounts') }} as order_discounts
    inner join non_test_orders
        on order_discounts.order_id = non_test_orders.order_id
    group by order_discounts.promotion_id

),

final as (

    select
        promotions.promotion_id::bigint as promotion_id,
        promotions.promotion_name::text as promotion_name,
        promotions.promotion_type::text as promotion_type,
        promotions.value::numeric(12, 2) as value,  -- noqa: RF04
        promotions.min_order_value::numeric(12, 2) as min_order_value,
        promotions.campaign_id::bigint as campaign_id,
        promotions.starts_at::timestamptz as starts_at,
        promotions.ends_at::timestamptz as ends_at,
        coalesce(coupon_codes.coupon_code_count, 0)::integer
            as coupon_code_count,
        coalesce(redemptions.redemption_count, 0)::integer
            as redemption_count,
        coalesce(redemptions.total_discount_amount, 0)::numeric(12, 2)
            as total_discount_amount
    from promotions
    left join coupon_codes
        on promotions.promotion_id = coupon_codes.promotion_id
    left join redemptions
        on promotions.promotion_id = redemptions.promotion_id

)

select * from final
