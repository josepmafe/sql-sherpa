with

source as (

    select * from {{ source('crm', 'email_sends') }}

),

renamed as (

    select
        id as email_send_id,
        lead_id,
        campaign_id,
        sent_at,
        opened_at,
        clicked_at
    from source

)

select * from renamed
