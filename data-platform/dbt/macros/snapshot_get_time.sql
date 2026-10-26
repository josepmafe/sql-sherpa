{#
    The wall-clock time dbt's snapshot materialization writes for a version that hard_deletes: invalidate closes.
    Every time column in this project is timestamptz, but dbt-postgres' default casts to `timestamp without time
    zone`, which mismatches the snapshot's columns (dbt warns on every run) and would be re-read in the session's
    time zone. dbt dispatches to this macro because it is in the root project.
#}
{% macro postgres__snapshot_get_time() -%}
    {{ current_timestamp() }}
{%- endmacro %}
