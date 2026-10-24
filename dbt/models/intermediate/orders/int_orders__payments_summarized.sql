with

payments as (

    select * from {{ ref('stg_shop__payments') }}

),

totals as (

    select
        order_id,
        count(*) as payment_count,
        coalesce(
            sum(amount) filter (where payment_status = 'captured'),
            0
        ) as captured_amount,
        count(*) filter (
            where payment_status = 'failed'
        ) as failed_payment_count
    from payments
    group by order_id

),

ranked_captures as (

    select
        order_id,
        method,
        row_number() over (
            partition by order_id
            order by amount desc, payment_id asc
        ) as capture_rank
    from payments
    where payment_status = 'captured'

),

primary_method as (

    select
        order_id,
        method as primary_payment_method
    from ranked_captures
    where capture_rank = 1

),

summarized as (

    select
        totals.order_id,
        totals.captured_amount,
        totals.payment_count,
        totals.failed_payment_count,
        primary_method.primary_payment_method
    from totals
    left join primary_method on totals.order_id = primary_method.order_id

)

select * from summarized
