{#-
    Lets the agent role read the marts: USAGE on the marts and extensions schemas, SELECT on
    every marts table. The marts' grants config already grants SELECT on each table on every
    target, but SELECT is useless without schema USAGE, which only prod and eval get here, so
    dev_marts stays closed to the agent.
    Runs as the on-run-end hook and as `dbt run-operation grant_agent_access`. It runs its SQL
    itself and returns '' so the hook has nothing left to execute.
-#}
{% macro grant_agent_access() -%}
    {%- if execute -%}
        {%- if target.name not in ['prod', 'eval'] -%}
            {%- do log('grant_agent_access: skipped on ' ~ target.name, info=true) -%}
        {#- `source freshness` also runs the hook, so on a fresh database it runs before any mart exists. -#}
        {%- elif not adapter.check_schema_exists(target.database, 'marts') -%}
            {%- do log('grant_agent_access: skipped on ' ~ target.name ~ ', schema marts not built yet', info=true) -%}
        {%- else -%}
            {%- set agent = adapter.quote(env_var('SHERPA_AGENT_USER')) -%}
            {%- set grants -%}
                grant usage on schema marts, extensions to {{ agent }};
                grant select on all tables in schema marts to {{ agent }};
            {%- endset -%}
            {%- do run_query(grants) -%}
            {%- do log('grant_agent_access: granted usage on marts, extensions and select on marts tables to '
                ~ agent ~ ' on ' ~ target.name, info=true) -%}
        {%- endif -%}
    {%- endif -%}
    {{- return('') -}}
{%- endmacro %}
