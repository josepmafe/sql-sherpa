-- Every discount comes from a promotion, so per promotion the rows of
-- fct_order_discounts add up to dim_promotions' redemption_count and
-- total_discount_amount (both include cancelled orders). Returns one row per
-- promotion where they differ.
with

discount_totals as (

    select
        promotion_id,
        count(*) as discount_count,
        sum(amount) as discount_amount
    from {{ ref('fct_order_discounts') }}
    group by promotion_id

)

select
    dim_promotions.redemption_count,
    dim_promotions.total_discount_amount,
    discount_totals.discount_count,
    discount_totals.discount_amount,
    coalesce(
        dim_promotions.promotion_id, discount_totals.promotion_id
    ) as promotion_id
from {{ ref('dim_promotions') }} as dim_promotions
full outer join discount_totals
    on dim_promotions.promotion_id = discount_totals.promotion_id
where
    coalesce(dim_promotions.redemption_count, 0)
    <> coalesce(discount_totals.discount_count, 0)
    or coalesce(dim_promotions.total_discount_amount, 0)
    <> coalesce(discount_totals.discount_amount, 0)
