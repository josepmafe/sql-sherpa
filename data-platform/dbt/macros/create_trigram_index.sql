{#-
    GIN trigram index on a text column, used as a post-hook by marts that support fuzzy search.
    Index names are unique per schema. While the post-hook runs, the previous build's table still
    exists as <name>__dbt_backup, holding its index under this same name, and dbt drops that backup
    (with the index) after the hooks. So the macro drops the same-named index first and then creates
    it on the new table; `if not exists` would see the backup's index and skip.
-#}
{% macro create_trigram_index(column) -%}
    {%- set index_name = this.identifier ~ '_' ~ column ~ '_trgm' -%}
    drop index if exists {{ this.schema }}.{{ index_name }};
    create index {{ index_name }}
    on {{ this }} using gin ({{ column }} extensions.gin_trgm_ops)
{%- endmacro %}
