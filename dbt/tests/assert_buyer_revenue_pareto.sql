{{ config(severity='warn') }}

-- Generator distribution check (spec 7): the top 20% of buyers by lifetime
-- net revenue hold 55-65% of all buyers' net revenue. Returns a row with the
-- measured share when it falls outside that band.
with

buyers as (

    select
        lifetime_net_revenue,
        row_number() over (
            order by lifetime_net_revenue desc, customer_id asc
        ) as revenue_rank,
        count(*) over () as buyer_count
    from {{ ref('dim_customers') }}
    where order_count >= 1

),

shares as (

    select
        sum(lifetime_net_revenue) filter (
            where revenue_rank <= ceil(0.20 * buyer_count)
        )
        / nullif(sum(lifetime_net_revenue), 0) as top_20_percent_share
    from buyers

)

select top_20_percent_share
from shares
where
    top_20_percent_share is null
    or top_20_percent_share < 0.55
    or top_20_percent_share > 0.65
