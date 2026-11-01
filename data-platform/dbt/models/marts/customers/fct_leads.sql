{{ config(materialized='view') }}

-- The published lead fact, email hashed (spec 6.5).
select
    lead_id,
    {{ mask_pii('email') }} as email,
    lead_source,
    lifecycle_stage,
    created_at,
    is_converted,
    converted_customer_id,
    converted_at,
    days_to_convert,
    emails_sent,
    emails_opened,
    emails_clicked
from {{ ref('fct_leads_unmasked') }}
