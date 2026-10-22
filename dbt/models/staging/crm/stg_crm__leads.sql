with

source as (

    select * from {{ source('crm', 'leads') }}

),

renamed as (

    select
        id as lead_id,
        source as lead_source,
        utm_campaign,
        lifecycle_stage,
        created_at,
        updated_at,
        lower(trim(email)) as email
    from source

)

select * from renamed
