{{ config(materialized='view') }}

with

refunds as (

    select * from {{ ref('stg_shop__refunds') }}

),

summarized as (

    select
        order_id,
        sum(amount) as refunded_amount,
        coalesce(
            sum({{ revenue_excluding_tax('amount') }}) filter (
                where refund_reason in ('return', 'goodwill')
            ),
            0
        ) as refunded_revenue
    from refunds
    group by order_id

)

select * from summarized
