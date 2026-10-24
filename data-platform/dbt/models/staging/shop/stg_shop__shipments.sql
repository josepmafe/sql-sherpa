with

source as (

    select * from {{ source('shop', 'shipments') }}

),

renamed as (

    select
        id as shipment_id,
        order_id,
        carrier,
        shipping_cost,
        shipped_at,
        delivered_at
    from source

)

select * from renamed
