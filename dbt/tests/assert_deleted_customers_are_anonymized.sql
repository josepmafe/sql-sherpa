-- Soft-deleted customers (R6) stay in dim_customers, so facts keep their
-- customer, but without their PII. Returns a row per deleted customer that
-- still has an email or a name, and one per customer whose is_deleted
-- disagrees with staging.
select
    dim_customers.customer_id,
    dim_customers.is_deleted,
    customers.is_deleted as staging_is_deleted
from {{ ref('dim_customers') }} as dim_customers
inner join {{ ref('stg_shop__customers') }} as customers
    on dim_customers.customer_id = customers.customer_id
where
    dim_customers.is_deleted is distinct from customers.is_deleted
    or (
        dim_customers.is_deleted
        and (
            dim_customers.email is not null
            or dim_customers.first_name is not null
            or dim_customers.last_name is not null
        )
    )
