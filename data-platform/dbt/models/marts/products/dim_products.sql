{#- Hooks are strings so {{ this }} renders per model run. -#}
{{
    config(
        post_hook=[
            "{{ create_trigram_index('product_name') }}",
            "{{ create_trigram_index('brand') }}",
            "{{ create_trigram_index('category_l1') }}",
            "{{ create_trigram_index('category_l2') }}",
            "{{ create_trigram_index('category_l3') }}",
        ]
    )
}}

with

variants as (

    select * from {{ ref('stg_shop__product_variants') }}

),

products as (

    select * from {{ ref('stg_shop__products') }}

),

categories as (

    select * from {{ ref('stg_shop__categories') }}

),

-- A product's category is a leaf at depth 2 or 3. Walking up the parent
-- links twice reaches the root; a depth-2 leaf has no grandparent, so l3 is
-- null and the leaf itself is l2.
hierarchy as (

    select
        leaf.category_id,
        coalesce(grandparent.category_name, parent.category_name)
            as category_l1,
        case
            when grandparent.category_id is null then leaf.category_name
            else parent.category_name
        end as category_l2,
        case
            when grandparent.category_id is null then null
            else leaf.category_name
        end as category_l3
    from categories as leaf
    left join categories as parent
        on leaf.parent_category_id = parent.category_id
    left join categories as grandparent
        on parent.parent_category_id = grandparent.category_id

),

final as (

    select
        variants.variant_id::bigint as variant_id,
        variants.sku::text as sku,
        variants.product_id::bigint as product_id,
        products.product_name::text as product_name,
        products.brand::text as brand,
        variants.size::text as size,
        variants.color::text as color,
        hierarchy.category_l1::text as category_l1,
        hierarchy.category_l2::text as category_l2,
        hierarchy.category_l3::text as category_l3,
        products.is_active::boolean as is_active,
        variants.list_price::numeric(12, 2) as current_list_price,
        variants.cost_price::numeric(12, 2) as current_cost_price,
        products.created_at::timestamptz as product_created_at
    from variants
    inner join products on variants.product_id = products.product_id
    left join hierarchy on products.category_id = hierarchy.category_id

)

select * from final
