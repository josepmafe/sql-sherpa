{#-
    Microbatch: each run reprocesses `lookback` + 1 daily batches of received_at (today and the 3 days
    before). On Postgres dbt runs every batch as a MERGE on unique_key, served by the unique index.
    `begin` comes from the environment because Postgres runs batches one after another: it must be the
    first simulated day, not an early fixed date that costs a batch per empty day on every fresh build.
-#}
{{
    config(
        materialized='incremental',
        incremental_strategy='microbatch',
        unique_key='event_id',
        event_time='received_at',
        batch_size='day',
        lookback=3,
        begin=(env_var('SHERPA_SIMULATION_START_DATE', '') or var('date_spine_start')),
        on_schema_change='fail',
        indexes=[{'columns': ['event_id'], 'unique': True}, {'columns': ['received_at']}]
    )
}}

with

source as (

    select * from {{ source('events', 'events') }}

),

-- The tracker can deliver an event twice (R1). Duplicates share event_id, so
-- numbering the rows per event_id and keeping the first removes them.
deduplicated as (

    select
        *,
        row_number() over (
            partition by event_id order by received_at
        ) as event_row_number
    from source

),

-- `->>` extracts a jsonb key as text (null if missing); values are then cast.
-- occurred_at is ISO-8601 text with an offset: casting to timestamptz applies
-- the offset and stores the instant in UTC.
renamed as (

    select
        event_id,
        anonymous_id,
        event_type,
        received_at,
        user_id::bigint as customer_id,
        occurred_at::timestamptz as occurred_at,
        context ->> 'device_type' as device_type,
        context ->> 'utm_source' as utm_source,
        context ->> 'utm_medium' as utm_medium,
        context ->> 'utm_campaign' as utm_campaign,
        context ->> 'referrer' as referrer,
        properties ->> 'page_path' as page_path,
        (properties ->> 'product_id')::bigint as product_id,
        (properties ->> 'variant_id')::bigint as variant_id,
        (properties ->> 'order_id')::bigint as order_id,
        (properties ->> 'cart_value')::numeric(12, 2) as cart_value
    from deduplicated
    where event_row_number = 1

)

select * from renamed
