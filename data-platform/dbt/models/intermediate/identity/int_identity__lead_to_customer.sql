with

customers as (

    select * from {{ ref('stg_shop__customers') }}

),

leads as (

    select * from {{ ref('stg_crm__leads') }}

),

-- Both emails are already normalized. The earliest lead per customer wins.
matched as (

    select
        customers.customer_id,
        leads.lead_id,
        leads.lead_source,
        row_number() over (
            partition by customers.customer_id
            order by leads.created_at asc, leads.lead_id asc
        ) as lead_rank
    from customers
    inner join leads
        on customers.email = leads.email

)

select
    customer_id,
    lead_id,
    lead_source
from matched
where lead_rank = 1
