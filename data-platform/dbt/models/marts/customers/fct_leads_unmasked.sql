with

leads as (

    select * from {{ ref('stg_crm__leads') }}

),

-- The published view, never dim_customers_unmasked: a lead converts when a
-- customer has its lead_id (spec 6.5), so a test account never converts.
customers as (

    select
        customer_id,
        lead_id,
        signup_at,
        is_deleted
    from {{ ref('dim_customers') }}

),

sends as (

    select
        lead_id,
        count(*) as emails_sent,
        count(opened_at) as emails_opened,
        count(clicked_at) as emails_clicked
    from {{ ref('stg_crm__email_sends') }}
    group by lead_id

),

joined as (

    select
        leads.lead_id,
        leads.lead_source,
        leads.lifecycle_stage,
        leads.created_at,
        customers.customer_id as converted_customer_id,
        customers.signup_at as converted_at,
        -- The deleted customer's lead loses its email as well (R6).
        case when customers.is_deleted then null else leads.email end
            as email,
        customers.customer_id is not null as is_converted,
        (customers.signup_at at time zone 'UTC')::date
        - (leads.created_at at time zone 'UTC')::date as days_to_convert,
        coalesce(sends.emails_sent, 0) as emails_sent,
        coalesce(sends.emails_opened, 0) as emails_opened,
        coalesce(sends.emails_clicked, 0) as emails_clicked
    from leads
    left join customers on leads.lead_id = customers.lead_id
    left join sends on leads.lead_id = sends.lead_id

),

final as (

    select
        lead_id::bigint as lead_id,
        email::text as email,
        lead_source::text as lead_source,
        lifecycle_stage::text as lifecycle_stage,
        created_at::timestamptz as created_at,
        is_converted::boolean as is_converted,
        converted_customer_id::bigint as converted_customer_id,
        converted_at::timestamptz as converted_at,
        days_to_convert::integer as days_to_convert,
        emails_sent::integer as emails_sent,
        emails_opened::integer as emails_opened,
        emails_clicked::integer as emails_clicked
    from joined

)

select * from final
