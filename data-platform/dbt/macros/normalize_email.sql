{% macro normalize_email(column) -%}
    lower(trim({{ column }}))
{%- endmacro %}
