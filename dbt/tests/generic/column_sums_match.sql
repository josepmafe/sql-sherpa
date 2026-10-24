{#
    Generic test: for every group_by value, the sum of model.column_name must equal the sum of
    compare_model.compare_column. Returns one row per group that differs. The full outer join makes a
    group missing on one side count as 0.
#}
{% test column_sums_match(model, column_name, compare_model, compare_column, group_by) %}

with

model_sums as (

    select
        {{ group_by }} as group_key,
        sum({{ column_name }}) as model_sum
    from {{ model }}
    group by {{ group_by }}

),

compare_sums as (

    select
        {{ group_by }} as group_key,
        sum({{ compare_column }}) as compare_sum
    from {{ compare_model }}
    group by {{ group_by }}

)

select
    coalesce(model_sums.group_key, compare_sums.group_key) as {{ group_by }},
    coalesce(model_sums.model_sum, 0) as model_sum,
    coalesce(compare_sums.compare_sum, 0) as compare_sum
from model_sums
full outer join compare_sums on model_sums.group_key = compare_sums.group_key
where coalesce(model_sums.model_sum, 0) <> coalesce(compare_sums.compare_sum, 0)

{% endtest %}
