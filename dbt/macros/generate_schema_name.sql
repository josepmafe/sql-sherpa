{#-
    dbt's built-in rule glues the custom schema onto the target schema (e.g. public_marts).
    Overriding this macro gives each target its own naming:
      - no custom schema   -> the target's schema
      - snapshots          -> always "snapshots": captured history cannot be rebuilt, so
                              there is exactly one copy that dev and prod both read
      - dev target         -> dev_<custom>
      - any other target   -> <custom> verbatim (prod)
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- elif node.resource_type == 'snapshot' -%}
        snapshots
    {%- elif target.name == 'dev' -%}
        dev_{{ custom_schema_name | trim }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
