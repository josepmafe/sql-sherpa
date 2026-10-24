{% macro revenue_excluding_tax(amount) -%}
    round({{ amount }} / (1 + {{ var('tax_rate') }}), 2)
{%- endmacro %}
