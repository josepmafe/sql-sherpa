-- Incremental history vs a full rebuild: compares fct_sessions in the
-- current target with the same table in another schema, e.g. after a
-- prod full refresh:
--   dbt show --select fct_sessions_incremental_vs_full_refresh
--     --vars '{compare_schema: marts}'
-- Zero rows means they match. compare_schema is read with a none default
-- because dbt compiles every analysis in build, compile and docs generate,
-- where a placeholder schema stands in. `dbt show` with this analysis in its
-- --select fails at compile time unless compare_schema is passed.
{% set compare_schema = var('compare_schema', none) %}
{% set selected = (invocation_args_dict.get('select') or []) | join(' ') %}
{% if compare_schema is none
    and flags.WHICH == 'show'
    and this.identifier in selected %}
    {{ exceptions.raise_compiler_error("Pass --vars '{compare_schema: marts}'") }}
{% endif %}

with

current_build as (

    select * from {{ ref('fct_sessions') }}

),

full_refresh as (

    select *
    from {{
        api.Relation.create(
            database=target.database,
            schema=compare_schema or 'compare_schema_not_set',
            identifier='fct_sessions'
        )
    }}

),

only_in_current as (

    select
        'current_only' as side,
        diff.*
    from (
        select * from current_build
        except
        select * from full_refresh
    ) as diff

),

only_in_full_refresh as (

    select
        'full_refresh_only' as side,
        diff.*
    from (
        select * from full_refresh
        except
        select * from current_build
    ) as diff

)

select * from only_in_current  -- noqa: AM04
union all
select * from only_in_full_refresh
