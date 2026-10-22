with

source as (

    select * from {{ source('shop', 'addresses') }}

),

country_codes as (

    select * from {{ ref('country_codes') }}

),

renamed as (

    select
        source.id as address_id,
        source.customer_id,
        source.type as address_type,
        source.city,
        source.postal_code,
        source.is_default,
        -- Same rule as stg_shop__customers: an unmapped raw value is kept
        -- so the relationships test fails.
        coalesce(country_codes.code, source.country_code) as country_code
    from source
    left join country_codes on source.country_code = country_codes.raw_value

)

select * from renamed
