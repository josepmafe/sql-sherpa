{#-
    Lets the reader roles (the agent and the UI) read the marts: USAGE on the marts and extensions schemas, SELECT on
    every marts table. The marts' grants config already grants SELECT on each table on every target, but SELECT is
    useless without schema USAGE, which only prod and eval get here, so dev_marts stays closed to both roles.
    The UI role also gets USAGE on ops and SELECT on ops.dbt_run_results (written by record_run_results), for its
    build status; the agent role never sees ops.
    Runs as the on-run-end hook and as `dbt run-operation grant_read_access`. It runs its SQL
    itself and returns '' so the hook has nothing left to execute.
-#}
{% macro grant_read_access() -%}
    {%- if execute -%}
        {%- if target.name not in ['prod', 'eval'] -%}
            {%- do log('grant_read_access: skipped on ' ~ target.name, info=true) -%}
        {#- `source freshness` also runs the hook, so on a fresh database it runs before any mart exists. -#}
        {%- elif not adapter.check_schema_exists(target.database, 'marts') -%}
            {%- do log('grant_read_access: skipped on ' ~ target.name ~ ', schema marts not built yet', info=true) -%}
        {%- else -%}
            {%- set agent = adapter.quote(validate_identifier(env_var('SHERPA_AGENT_USER'), 'SHERPA_AGENT_USER')) -%}
            {%- set ui = adapter.quote(validate_identifier(env_var('SHERPA_UI_USER'), 'SHERPA_UI_USER')) -%}
            {%- for reader in [agent, ui] -%}
                {%- set grants -%}
                    grant usage on schema marts, extensions to {{ reader }};
                    grant select on all tables in schema marts to {{ reader }};
                {%- endset -%}
                {%- do run_query(grants) -%}
                {%- do log('grant_read_access: granted usage on marts, extensions and select on marts tables to '
                    ~ reader ~ ' on ' ~ target.name, info=true) -%}
            {%- endfor -%}
            {%- if adapter.get_relation(target.database, 'ops', 'dbt_run_results') -%}
                {%- do run_query('grant usage on schema ops to ' ~ ui ~ '; grant select on ops.dbt_run_results to ' ~ ui ~ ';') -%}
                {%- do log('grant_read_access: granted usage on ops and select on ops.dbt_run_results to '
                    ~ ui ~ ' on ' ~ target.name, info=true) -%}
            {%- else -%}
                {%- do log('grant_read_access: ops.dbt_run_results not built yet, no ops grants to ' ~ ui, info=true) -%}
            {%- endif -%}
        {%- endif -%}
    {%- endif -%}
    {{- return('') -}}
{%- endmacro %}
