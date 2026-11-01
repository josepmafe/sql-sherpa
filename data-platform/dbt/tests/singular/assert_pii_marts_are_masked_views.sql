{{ config(group='customers') }}
-- Spec 6.5 "Checks": a model with a meta.pii column is a table named
-- *_unmasked behind a plain view that hashes it. Failing rows are
-- (model, problem) and hold literals only; the arm that reads the table
-- returns no value of its PII columns.
-- depends_on: {{ ref('dim_customers') }}
-- depends_on: {{ ref('dim_customers_unmasked') }}
-- depends_on: {{ ref('fct_leads') }}
-- depends_on: {{ ref('fct_leads_unmasked') }}
-- noqa: disable=LT02,LT05
{% set arms = [] -%}
{%- if execute -%}
{#- a model other than the view of an *_unmasked table reads it (another *_unmasked model included) -#}
{%- for reader in graph.nodes.values() | selectattr('resource_type', 'equalto', 'model') -%}
{%- for dep_id in reader.depends_on.nodes if dep_id.endswith('_unmasked') and dep_id.split('.')[-1][:-9] != reader.name -%}
{%- do arms.append("select '" ~ reader.name ~ "', 'reads " ~ dep_id.split('.')[-1] ~ "; ref " ~ dep_id.split('.')[-1][:-9] ~ " instead'") -%}
{%- endfor -%}
{%- endfor -%}
{%- for table in graph.nodes.values() | selectattr('resource_type', 'equalto', 'model') -%}
{%- set pii = table.columns.values() | selectattr('meta.pii') | map(attribute='name') | list -%}
{%- if pii -%}
{%- if not (table.name.endswith('_unmasked') and table.config.materialized == 'table') -%}
{%- do arms.append("select '" ~ table.name ~ "', 'has a meta.pii column but is not a table named *_unmasked'") -%}
{%- else -%}
{%- set view_name = table.name[:-9] -%}
{%- set view = graph.nodes.get('model.' ~ table.package_name ~ '.' ~ view_name) -%}
{%- if view is none or view.config.materialized != 'view' -%}
{%- do arms.append("select '" ~ table.name ~ "', 'its sibling " ~ view_name ~ " is missing from the project or is not a view'") -%}
{%- else -%}
{#- (c) what Postgres holds, in literals-only arms -#}
{%- set view_rel = "to_regclass('" ~ view.relation_name ~ "')" -%}
{%- set table_rel = "to_regclass('" ~ table.relation_name ~ "')" -%}
{%- do arms.append("select '" ~ view_name ~ "', 'missing in Postgres' where " ~ view_rel ~ " is null") -%}
{%- do arms.append("select '" ~ table.name ~ "', 'missing in Postgres' where " ~ table_rel ~ " is null") -%}
{%- do arms.append("select '" ~ view_name ~ "', 'is not a plain view' from pg_class where oid = " ~ view_rel ~ " and (relkind <> 'v' or reloptions is not null)") -%}
{%- do arms.append("select '" ~ table.name ~ "', 'is not a table' from pg_class where oid = " ~ table_rel ~ " and relkind <> 'r'") -%}
{%- set attrs = "(select attname::text from pg_attribute where attnum > 0 and not attisdropped and attrelid = " -%}
{%- set pii_list = [] -%}
{%- for column in pii -%}
{%- do pii_list.append("'" ~ column ~ "'") -%}
{%- endfor -%}
{#- the view's columns are the table's, in the table's order, minus pii columns the view leaves out -#}
{%- do arms.append("select '" ~ view_name ~ "', 'has a column that " ~ table.name ~ " lacks, or the table columns in another order' where (select array_agg(attname::text order by attnum) from pg_attribute where attnum > 0 and not attisdropped and attrelid = " ~ view_rel ~ ") is distinct from (select array_agg(attname::text order by attnum) from pg_attribute where attnum > 0 and not attisdropped and attrelid = " ~ table_rel ~ " and attname::text in " ~ attrs ~ view_rel ~ "))") -%}
{%- do arms.append("select '" ~ view_name ~ "', 'leaves out a column of " ~ table.name ~ " that is not pii' where exists (select 1 from pg_attribute where attnum > 0 and not attisdropped and attrelid = " ~ table_rel ~ " and attname::text <> all(array[" ~ pii_list | join(', ') ~ "]) and attname::text not in " ~ attrs ~ view_rel ~ "))") -%}
{#- (d) the view marks as masked exactly the pii columns it exposes -#}
{%- set masked = view.columns.values() | selectattr('meta.masked') | map(attribute='name') | list -%}
{%- for column in pii if column in view.columns and column not in masked -%}
{%- do arms.append("select '" ~ view_name ~ "', 'column " ~ column ~ " is pii in " ~ table.name ~ " but not masked here'") -%}
{%- endfor -%}
{%- for column in masked if column not in pii -%}
{%- do arms.append("select '" ~ view_name ~ "', 'column " ~ column ~ " is masked here but not pii in " ~ table.name ~ "'") -%}
{%- endfor -%}
{#- (e) the view must show the salted hash of the table's value, wherever the table has one -#}
{%- if adapter.get_relation(view.database, view.schema, view.alias) and adapter.get_relation(table.database, table.schema, table.alias) -%}
{%- set keys = [view.meta.primary_key] if view.meta.primary_key is string else view.meta.primary_key -%}
{%- set on = [] -%}
{%- for key in keys -%}
{%- do on.append("v." ~ adapter.quote(key) ~ " = t." ~ adapter.quote(key)) -%}
{%- endfor -%}
{%- for column in masked -%}
{%- set quoted = adapter.quote(column) -%}
{%- do arms.append("select '" ~ view_name ~ "', 'column " ~ column ~ " is not the salted hash of the real value where there is one' where exists (select 1 from " ~ view.relation_name ~ " as v inner join " ~ table.relation_name ~ " as t on " ~ on | join(' and ') ~ " where t." ~ quoted ~ " is not null and (v." ~ quoted ~ " is null or v." ~ quoted ~ " is distinct from " ~ mask_pii('t.' ~ quoted) ~ "))") -%}
{%- endfor -%}
{%- endif -%}
{#- (f) this test must run after both, so it is ordered behind their builds -#}
{%- if view.unique_id not in model.depends_on.nodes or table.unique_id not in model.depends_on.nodes -%}
{%- do arms.append("select '" ~ view_name ~ "', 'add -- depends_on lines for " ~ view_name ~ " and " ~ table.name ~ " to this test'") -%}
{%- endif -%}
{%- endif -%}
{%- endif -%}
{%- endif -%}
{%- endfor -%}
{%- endif -%}
select
    null::text as model, -- noqa: RF04
    null::text as problem
where false
{% for arm in arms %}
union all
{{ arm }}
{% endfor %}
