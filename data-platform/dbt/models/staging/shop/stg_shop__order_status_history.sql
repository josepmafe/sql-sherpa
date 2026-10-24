with

source as (

    select * from {{ source('shop', 'order_status_history') }}

),

renamed as (

    select
        id as status_change_id,
        order_id,
        status,
        changed_at
    from source

)

select * from renamed
