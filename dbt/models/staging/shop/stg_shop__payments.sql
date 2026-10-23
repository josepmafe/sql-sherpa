with

source as (

    select * from {{ source('shop', 'payments') }}

),

renamed as (

    select
        id as payment_id,
        order_id,
        method,
        amount,
        status as payment_status,
        created_at
    from source

)

select * from renamed
