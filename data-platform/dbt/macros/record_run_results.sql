{#-
    Appends one row per executed node of this invocation to ops.dbt_run_results (created here if missing), on every
    target. dbt rewrites run_results.json on each command that compiles, so the file cannot tell which build was the
    last full one; this table keeps them all. is_full_run is true when the invocation had no --select, --exclude
    or --selector.
    Runs as the on-run-end hook, before grant_read_access so the table exists when the UI role is granted on it.
    It runs its SQL itself and returns '' so the hook has nothing left to execute.
-#}
{% macro record_run_results(results) -%}
    {%- if execute -%}
        {%- set args = invocation_args_dict -%}
        {%- set is_full_run = not (args.get('select') or args.get('exclude') or args.get('selector')) -%}
        {%- set rows = [] -%}
        {%- for result in results -%}
            {#- Freshness results carry the source as `node` too; a result without one has nothing to record. -#}
            {%- if result.node is defined and result.node -%}
                {%- set row -%}
                    ({{ _audit_text(invocation_id) }}, {{ _audit_text(target.name) }}, {{ _audit_text(flags.WHICH) }},
                    {{ is_full_run }}, {{ _audit_text(result.node.unique_id) }},
                    {{ _audit_text(result.node.resource_type) }}, {{ _audit_text(result.status) }},
                    {{ result.failures if result.failures is not none else 'null' }},
                    {{ result.execution_time if result.execution_time is not none else 'null' }},
                    {{ _audit_text((result.message | string)[:2000] if result.message is not none else none) }},
                    {{ _audit_text(run_started_at.isoformat()) }}::timestamptz)
                {%- endset -%}
                {%- do rows.append(row) -%}
            {%- endif -%}
        {%- endfor -%}
        {%- set statements -%}
            create schema if not exists ops;
            create table if not exists ops.dbt_run_results (
                invocation_id text not null,
                target_name text not null,
                command text not null,
                is_full_run boolean not null,
                unique_id text not null,
                resource_type text not null,
                status text not null,
                failures integer,
                execution_time_s double precision,
                message text,
                run_started_at timestamptz not null
            );
            {% if rows -%}
            insert into ops.dbt_run_results (
                invocation_id, target_name, command, is_full_run, unique_id, resource_type, status, failures,
                execution_time_s, message, run_started_at
            ) values {{ rows | join(', ') }};
            {%- endif %}
        {%- endset -%}
        {%- do run_query(statements) -%}
        {%- do log('record_run_results: recorded ' ~ rows | length ~ ' results of invocation ' ~ invocation_id
            ~ ' on ' ~ target.name, info=true) -%}
    {%- endif -%}
    {{- return('') -}}
{%- endmacro %}

{#- A SQL string literal, or null; doubles single quotes (standard_conforming_strings is on, so backslashes stay literal). -#}
{% macro _audit_text(value) -%}
    {%- if value is none -%}null{%- else -%}'{{ (value | string) | replace("'", "''") }}'{%- endif -%}
{%- endmacro %}
