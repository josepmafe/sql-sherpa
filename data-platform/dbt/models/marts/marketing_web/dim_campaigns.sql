with

campaigns as (

    select * from {{ ref('stg_marketing__campaigns') }}

),

final as (

    select
        campaign_id::bigint as campaign_id,
        campaign_name::text as campaign_name,
        utm_campaign::text as utm_campaign,
        channel::text as channel,
        starts_at::timestamptz as starts_at,
        ends_at::timestamptz as ends_at,
        budget::numeric(12, 2) as budget
    from campaigns

)

select * from final
