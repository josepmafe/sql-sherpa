with

source as (

    select * from {{ source('marketing', 'ad_spend_daily') }}

),

renamed as (

    select
        spend_date,
        campaign_id,
        spend,
        impressions,
        clicks
    from source

)

select * from renamed
