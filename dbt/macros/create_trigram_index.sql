{#- GIN trigram index on a text column, used as a post-hook by marts that support fuzzy search. -#}
{% macro create_trigram_index(column) -%}
    create index if not exists {{ this.identifier }}_{{ column }}_trgm
    on {{ this }} using gin ({{ column }} extensions.gin_trgm_ops)
{%- endmacro %}
