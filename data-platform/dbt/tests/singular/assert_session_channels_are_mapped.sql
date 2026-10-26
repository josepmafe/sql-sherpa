{{ config(severity='error', warn_if='>0', error_if='>100') }}

-- Returns the sessions whose utm pair has no channel_mapping row. A few mean
-- channel_mapping lacks a new UTM pair (add it); over 100 mean the tracking is
-- broken.
select
    session_id,
    utm_source,
    utm_medium
from {{ ref('fct_sessions') }}
where marketing_channel = 'unknown'
