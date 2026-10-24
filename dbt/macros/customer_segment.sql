{#- Lifecycle segment of a customer (spec 6.5). order_count counts
    non-cancelled orders; days_since_last_order is null when it is 0. -#}
{% macro customer_segment(order_count, days_since_last_order) -%}
    case
        when {{ order_count }} = 0 then 'prospect'
        when
            {{ days_since_last_order }} <= 90 and {{ order_count }} = 1
            then 'new'
        when {{ days_since_last_order }} <= 90 then 'active'
        when {{ days_since_last_order }} <= 180 then 'at_risk'
        else 'churned'
    end
{%- endmacro %}
