with

source as (

    select * from {{ source('shop', 'orders') }}

),

renamed as (

    select
        id as order_id,
        order_number,
        customer_id,
        shipping_address_id,
        status as order_status,
        subtotal,
        discount_total,
        shipping_amount,
        tax_amount,
        order_total,
        sales_channel,
        placed_at,
        updated_at
    from source

)

select * from renamed
