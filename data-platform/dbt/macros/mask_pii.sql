{#-
    Pseudonymises a column: md5 of the value and the secret salt in ops.pii_salt (ensure_pii_salt creates it).
    Equal values hash equally, so counts, joins and group by still work; NULL stays NULL.
    The salt is read inside Postgres, so it never appears in dbt's artifacts or logs, and the table lives outside
    the dbt project, so the relation is built by hand.
-#}
{% macro mask_pii(column) -%}
    md5({{ column }} || (select salt from {{ api.Relation.create(database=target.database, schema='ops', identifier='pii_salt') }}))
{%- endmacro %}
