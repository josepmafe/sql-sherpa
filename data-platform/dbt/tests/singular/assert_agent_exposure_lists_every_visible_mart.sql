-- Spec 6.5 "Checks": the sherpa_agent exposure lists exactly the visible
-- marts, the ones grant_read_access gives the agent (schema marts, name not
-- ending in _unmasked). A visible mart must be public and an _unmasked table
-- private with no select grant. Failing rows are (unique_id, problem),
-- literals only.
-- noqa: disable=LT02,LT05
{% set arms = [] -%}
{%- if execute -%}
{%- set exposure = graph.exposures.get('exposure.sherpa.sherpa_agent') -%}
{%- set listed = exposure.depends_on.nodes if exposure else [] -%}
{%- set visible = [] -%}
{%- for node in graph.nodes.values() | selectattr('resource_type', 'equalto', 'model') if node.schema.endswith('marts') -%}
{%- if node.name.endswith('_unmasked') -%}
{%- if node.access | string != 'private' -%}
{%- do arms.append("select '" ~ node.unique_id ~ "', 'unmasked but not private'") -%}
{%- endif -%}
{%- if node.config.grants.get('select') -%}
{%- do arms.append("select '" ~ node.unique_id ~ "', 'unmasked but granted'") -%}
{%- endif -%}
{%- else -%}
{%- do visible.append(node.unique_id) -%}
{%- if node.unique_id not in listed -%}
{%- do arms.append("select '" ~ node.unique_id ~ "', 'not in sherpa_agent'") -%}
{%- endif -%}
{%- if node.access | string != 'public' -%}
{%- do arms.append("select '" ~ node.unique_id ~ "', 'visible but not public'") -%}
{%- endif -%}
{%- endif -%}
{%- endfor -%}
{%- for unique_id in listed if unique_id not in visible -%}
{%- do arms.append("select '" ~ unique_id ~ "', 'not a visible mart'") -%}
{%- endfor -%}
{%- endif -%}
select
    null::text as unique_id, -- noqa: RF04
    null::text as problem
where false
{% for arm in arms %}
union all
{{ arm }}
{% endfor %}
