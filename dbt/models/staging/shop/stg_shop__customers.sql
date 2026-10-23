-- ref() points to another dbt model or seed; source() points to a raw table
-- declared in a sources YAML. dbt resolves both to real table names and uses
-- them to build the DAG.
with

source as (

    select * from {{ source('shop', 'customers') }}

),

country_variants as (

    select * from {{ ref('country_code_variants') }}

),

renamed as (

    select
        source.id as customer_id,
        source.first_name,
        source.last_name,
        source.marketing_opt_in,
        source.created_at as signup_at,
        source.updated_at,
        source.deleted_at,
        lower(trim(source.email)) as email,
        -- Left join so a customer is never dropped. A raw country missing
        -- from the seed keeps its raw value (not null, which the
        -- relationships test would skip), so the test fails loudly and the
        -- seed gets fixed.
        coalesce(country_variants.code, source.country_code) as country_code,
        (
            source.is_test_account
            or lower(trim(source.email)) like '%@sqlsherpa-test.com'
        ) as is_test_account,
        (source.deleted_at is not null) as is_deleted
    from source
    left join country_variants
        on source.country_code = country_variants.raw_value

)

select * from renamed
