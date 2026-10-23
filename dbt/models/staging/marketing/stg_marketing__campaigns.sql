with

source as (

    select * from {{ source('marketing', 'campaigns') }}

),

renamed as (

    select
        id as campaign_id,
        name as campaign_name,
        utm_campaign,
        channel,
        starts_at,
        ends_at,
        budget
    from source

)

select * from renamed
