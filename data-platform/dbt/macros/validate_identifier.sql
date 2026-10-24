{#-
    Returns name if it is a plain lower-case Postgres identifier, and fails the run otherwise.
    Role and database names from .env are spliced into GRANT and ALTER DATABASE statements
    (dbt's grants config does not quote grantees), so anything else stops the run here.
-#}
{% macro validate_identifier(name, label) -%}
    {%- if not modules.re.fullmatch('[a-z_][a-z0-9_]*', name) -%}
        {{- exceptions.raise_compiler_error(
            label ~ " must match ^[a-z_][a-z0-9_]*$, got '" ~ name ~ "'"
        ) -}}
    {%- endif -%}
    {{- return(name) -}}
{%- endmacro %}
