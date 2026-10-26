{{ config(severity='error', warn_if='>0', error_if='>100') }}

-- Returns the sessions whose utm pair has no channel_mapping row. A few mean
-- channel_mapping lacks a new UTM pair: add it, then run
-- `dbt build --full-refresh --select int_events__sessionized+` on each target,
-- otherwise older sessions and fct_orders.marketing_channel /
-- acquisition_channel stay `unknown`. Over 100 mean the tracking is broken.
select
    session_id,
    utm_source,
    utm_medium
from {{ ref('fct_sessions') }}
where marketing_channel = 'unknown'
