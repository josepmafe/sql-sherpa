{#-
    Installs pg_trgm (trigram fuzzy matching) in its own schema and puts that schema on
    the database search_path, so the % operator and similarity() resolve without a prefix.
    The search_path change only applies to new sessions.
    As the first hook of every run it also checks the database name and the agent and UI role names,
    so a malformed name stops the run before any model's grants config uses it.
-#}
{% macro ensure_extensions() -%}
    {%- set dbname = validate_identifier(target.dbname, 'The target database name') -%}
    {%- do validate_identifier(env_var('SHERPA_AGENT_USER', 'sherpa_agent'), 'SHERPA_AGENT_USER') -%}
    {%- do validate_identifier(env_var('SHERPA_UI_USER', 'sherpa_ui'), 'SHERPA_UI_USER') -%}
    create schema if not exists extensions;
    create extension if not exists pg_trgm with schema extensions;
    alter database "{{ dbname }}"
    set search_path = "$user", public, extensions;
{%- endmacro %}
