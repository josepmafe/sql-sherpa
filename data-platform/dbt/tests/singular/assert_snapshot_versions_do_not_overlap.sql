-- A variant has at most one open version (null dbt_valid_to) and its versions
-- never overlap in time. The snapshot is the only record of past prices and
-- costs, so a duplicated open version or an overlap would make
-- int_order_items__costed pick (or fan out) the wrong cost. Returns one row per
-- id with two open versions and one per pair of overlapping versions; dbt fails
-- the test if any row comes back. The failing rows are stored in test_failures
-- (folder config in dbt_project.yml).
with versions as (

    select
        id,
        dbt_valid_from,
        dbt_valid_to
    from {{ ref('snap_product_variants') }}

),

two_open_versions as (

    select
        id,
        'more than one open version' as problem
    from versions
    where dbt_valid_to is null
    group by id
    having count(*) > 1

),

overlapping_versions as (

    select
        earlier.id,
        'overlapping validity ranges' as problem
    from versions as earlier
    inner join versions as later
        on
            earlier.id = later.id
            and earlier.dbt_valid_from < later.dbt_valid_from
            and coalesce(earlier.dbt_valid_to, 'infinity'::timestamp)
            > later.dbt_valid_from

)

select * from two_open_versions
union all
select * from overlapping_versions
