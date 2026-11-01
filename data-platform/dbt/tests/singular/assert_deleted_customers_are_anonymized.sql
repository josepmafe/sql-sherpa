{{ config(group='customers') }}
-- Soft-deleted customers (R6) stay in dim_customers_unmasked (the source of
-- truth), so facts keep their customer, but without their PII. Returns a row
-- per deleted customer that still has an email or a name, and one per
-- customer whose is_deleted disagrees with staging. A lead whose customer is
-- deleted must have no email either: its row is the lead_id, never the email.
select
    dim_customers_unmasked.customer_id,
    dim_customers_unmasked.is_deleted,
    customers.is_deleted as staging_is_deleted,
    null::bigint as lead_id
from {{ ref('dim_customers_unmasked') }} as dim_customers_unmasked
inner join {{ ref('stg_shop__customers') }} as customers
    on dim_customers_unmasked.customer_id = customers.customer_id
where
    dim_customers_unmasked.is_deleted is distinct from customers.is_deleted
    or (
        dim_customers_unmasked.is_deleted
        and (
            dim_customers_unmasked.email is not null
            or dim_customers_unmasked.first_name is not null
            or dim_customers_unmasked.last_name is not null
        )
    )

union all

select
    leads.converted_customer_id as customer_id,
    true as is_deleted,
    null::boolean as staging_is_deleted,
    leads.lead_id
from {{ ref('fct_leads_unmasked') }} as leads
inner join {{ ref('dim_customers_unmasked') }} as customers
    on leads.converted_customer_id = customers.customer_id
where
    customers.is_deleted
    and leads.email is not null
