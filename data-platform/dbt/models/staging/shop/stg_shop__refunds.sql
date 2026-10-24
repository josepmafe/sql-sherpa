with

source as (

    select * from {{ source('shop', 'refunds') }}

),

renamed as (

    select
        id as refund_id,
        order_id,
        return_id,
        payment_id,
        amount,
        reason as refund_reason,
        refunded_at
    from source

)

select * from renamed
