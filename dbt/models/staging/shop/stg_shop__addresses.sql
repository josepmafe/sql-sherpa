with

source as (

    select * from {{ source('shop', 'addresses') }}

),

country_variants as (

    select * from {{ ref('country_code_variants') }}

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
        coalesce(country_variants.code, source.country_code) as country_code
    from source
    left join country_variants
        on source.country_code = country_variants.raw_value

)

select * from renamed
