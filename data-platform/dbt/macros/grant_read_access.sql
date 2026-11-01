{#-
    Lets the reader roles (the agent and the UI) read the marts: USAGE on the marts and extensions schemas, and SELECT
    on every relation of marts that is safe to read (spec §6.5, "PII masking"). dev_marts stays closed to both roles
    because only prod and eval get the schema USAGE.
    Order of the grants, so the hook fails closed:
      1. Every relation named *_unmasked (a table with real PII) is revoked from both roles and public first.
      2. Each graph model of marts that is not *_unmasked gets SELECT, unless it is not built (skipped, logged) or
         it has an *_unmasked sibling and is not a view (a table is an older build's raw values): then it is
         revoked.
    The UI role also gets USAGE on ops and SELECT on ops.dbt_run_results (written by record_run_results), for its
    build status; the agent role never sees ops, nor ops.pii_salt.
    Runs as the on-run-end hook and as `dbt run-operation grant_read_access`. It runs its SQL
    itself and returns '' so the hook has nothing left to execute. It never fails on a missing mart or view.
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
            {%- set readers = agent ~ ', ' ~ ui -%}
            {%- for reader in [agent, ui] -%}
                {%- do run_query('grant usage on schema marts, extensions to ' ~ reader ~ ';') -%}
                {%- do log('grant_read_access: granted usage on marts and extensions to ' ~ reader ~ ' on ' ~ target.name, info=true) -%}
            {%- endfor -%}
            {%- set built = {} -%}
            {%- for row in list_relations_without_caching(api.Relation.create(database=target.database, schema='marts')) -%}
                {%- do built.update({row['name']: row['type']}) -%}
            {%- endfor -%}
            {%- set models = graph.nodes.values() | selectattr('resource_type', 'equalto', 'model') | selectattr('schema', 'equalto', 'marts') | list -%}
            {%- set modelled = models | map(attribute='name') | list -%}
            {#- The unmasked tables first, so no later failure can leave a reader on real PII. -#}
            {%- for name in built if name.endswith('_unmasked') -%}
                {%- do run_query('revoke all on marts.' ~ adapter.quote(name) ~ ' from ' ~ readers ~ ', public;') -%}
                {%- do log('grant_read_access: revoked all on marts.' ~ name ~ ' from ' ~ readers ~ ' and public on ' ~ target.name, info=true) -%}
            {%- endfor -%}
            {%- for name in modelled if not name.endswith('_unmasked') -%}
                {%- if name not in built -%}
                    {%- do log('grant_read_access: marts.' ~ name ~ ' not built yet, no grant on ' ~ target.name, info=true) -%}
                {%- elif (name ~ '_unmasked') in modelled and built[name] != 'view' -%}
                    {%- do run_query('revoke all on marts.' ~ adapter.quote(name) ~ ' from ' ~ readers ~ ', public;') -%}
                    {%- do log('grant_read_access: revoked all on marts.' ~ name ~ ' (not a view, so an older build with real PII) from '
                        ~ readers ~ ' and public on ' ~ target.name, info=true) -%}
                {%- else -%}
                    {%- do run_query('grant select on marts.' ~ adapter.quote(name) ~ ' to ' ~ readers ~ ';') -%}
                    {%- do log('grant_read_access: granted select on marts.' ~ name ~ ' to ' ~ readers ~ ' on ' ~ target.name, info=true) -%}
                {%- endif -%}
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
