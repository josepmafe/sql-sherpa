{#-
    Installs pg_trgm (trigram fuzzy matching) in its own schema and puts that schema on
    the database search_path, so the % operator and similarity() resolve without a prefix.
    The search_path change only applies to new sessions.
-#}
{% macro ensure_extensions() -%}
    create schema if not exists extensions;
    create extension if not exists pg_trgm with schema extensions;
    alter database "{{ target.dbname }}"
    set search_path = "$user", public, extensions;
{%- endmacro %}
