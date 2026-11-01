{{ config(materialized='view') }}

-- The published customer dimension, PII pseudonymised (spec 6.5).
select
    customer_id,
    {{ mask_pii('email') }} as email,
    {{ mask_pii('first_name') }} as first_name,
    {{ mask_pii('last_name') }} as last_name,
    country_code,
    country_name,
    region,
    signup_at,
    marketing_opt_in,
    is_deleted,
    lead_id,
    lead_source,
    acquisition_channel,
    acquisition_campaign_id,
    first_order_at,
    last_order_at,
    order_count,
    lifetime_gross_revenue,
    lifetime_net_revenue,
    lifetime_refunded_revenue,
    avg_order_value,
    days_since_last_order,
    customer_segment,
    is_vip
from {{ ref('dim_customers_unmasked') }}
