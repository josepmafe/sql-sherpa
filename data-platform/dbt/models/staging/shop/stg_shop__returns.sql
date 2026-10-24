with

source as (

    select * from {{ source('shop', 'returns') }}

),

renamed as (

    select
        id as return_id,
        order_id,
        reason as return_reason,
        status as return_status,
        requested_at,
        updated_at
    from source

)

select * from renamed
