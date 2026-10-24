# Adding or changing a mart

How to change what the marts expose: add a column, add a whole mart, change a metric, add a trigram index, write a custom test, and choose how an intermediate model is materialized. Each section lists the files to touch, in order, and the tests that prove the change. The examples were applied on a scratch copy of the `dbt/` folder and the outputs are real. Take the dbt commands as run from `dbt/` with the usual prefix (`DBT_PROFILES_DIR=. uv run --env-file ../.env dbt`, shortened to `dbt`; see [Building the warehouse](../basic/building-the-warehouse.md#running-dbt-directly)).

## Before you start

**The spec comes first.** The marts' columns, grain and metric definitions are in the data platform spec (`docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`, §6.5 and §6.6). A change to them is a spec change: agree it with the owner and update the spec in the same branch. The text-to-SQL agent relies on the marts as the spec's §11 describes them, so a rename or a removal breaks it.

**What makes a mart a mart.** Everything under `dbt/models/marts/` inherits these settings from `dbt/dbt_project.yml`:

- `materialized: table` in the `marts` schema (`dev_marts` on dev).
- `contract: enforced`: dbt checks the model's columns and types against the YAML before it builds the table. A *contract* is a promise in the YAML about the shape of the table.
- `persist_docs`: descriptions are copied into Postgres comments, where the agent reads them.
- `post-hook: analyze`: table statistics are refreshed after each build.
- `grants`: `SELECT` for the `sherpa_agent` role on every mart table.

The folder is `marts/<domain>/`, with one `_<domain>__models.yml` per folder and the `meta.domain` value matching the folder (`sales`, `customers`, `products`, `shared`). Metric definitions live in `dbt/models/marts/_metrics.md`.

## 1. Add a column to a mart

Example: `dim_products.current_margin`, the current list price minus the current cost price. Three files, in this order.

**1. The SQL** (`dbt/models/marts/products/dim_products.sql`). Add the column to the model's last CTE (the one named `final`), with an explicit cast to the type you will declare. Casting in the last step keeps every mart's types visible in one place:

```sql
        variants.list_price::numeric(12, 2) as current_list_price,
        (variants.list_price - variants.cost_price)::numeric(12, 2) as current_margin,
        variants.cost_price::numeric(12, 2) as current_cost_price,
```

**2. The YAML** (`dbt/models/marts/products/_products__models.yml`), in the same position:

```yaml
      - name: current_margin
        description: Current list price minus current cost price of the variant in EUR.
        data_type: numeric(12,2)
        config:
          meta:
            synonyms: [unit margin]
        data_tests: [not_null]
```

- `data_type` is mandatory on a contracted model and must match the SQL.
- `description` is mandatory in every model and column (`make docs-check` enforces it).
- `meta.synonyms` lists the words people use for the column, so the agent can match a question to it. Add `meta.pii: true` for personal data.
- `data_tests` are the tests that prove the column: `not_null`, `unique`, `accepted_values` for a closed set, `relationships` for a foreign key.
- For a money metric, reuse its doc block in the description: `{{ doc('metric_net_revenue') }}`.

**3. Build and test the model:**

```bash
dbt build --select dim_products
```

```
22:28:34  Finished running 2 project hooks, 1 table model, 9 data tests in 0 hours 0 minutes and 1.13 seconds (1.13s).
22:28:34  Completed successfully
22:28:34  Done. PASS=12 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=12
```

The 9 tests are the model's own, the new `not_null` included, plus the tests on other models that reference it (here the `relationships` test of `fct_order_items`, which `--select` brings along). Check the type Postgres gave the column:

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa -c '\d dev_marts.dim_products' | grep current_margin
```

```
 current_margin     | numeric(12,2)            |           |          |
```

dbt creates the table with the types of the YAML and then inserts the query into it, which is why the contract matters: the YAML decides the real column type.

### What a contract error looks like

Forget the YAML and the build stops before creating anything:

```
  This model has an enforced contract that failed.
  Please ensure the name, data_type, and number of columns in your contract match the columns in your model's definition.

  | column_name  | definition_type | contract_type | mismatch_reason     |
  | ------------ | --------------- | ------------- | ------------------- |
  | double_price | DECIMAL         |               | missing in contract |
```

Forget the `data_type` and dbt names the column:

```
  Contracted models require data_type to be defined for each column. Please ensure that the column name and data_type are defined within the YAML configuration for the ['current_margin'] column(s).
```

Declare a type the SQL does not produce (here the SQL ends in `::text`, the YAML says `numeric(12,2)`):

```
  | column_name    | definition_type | contract_type | mismatch_reason    |
  | -------------- | --------------- | ------------- | ------------------ |
  | current_margin | TEXT            | DECIMAL       | data type mismatch |
```

`definition_type` is what the SQL produces; `contract_type` is what the YAML says. Fix the SQL cast or the YAML, never both to "whatever works".

### Changes that touch other models

- A column that depends on a **new source column** needs the source model and the staging model first: [Extending the source data model](extending-the-source-data-model.md) ends here.
- A column used by **another mart** (for example `fct_orders` columns that `dim_customers` aggregates) must exist in the upstream mart before the downstream one reads it. `dbt build --select fct_orders+` rebuilds the model and everything downstream.
- A **new test** that pins the column's logic goes in `dbt/tests/` (see section 3) or in the YAML.

Run `make build`, then `make docs-check` (see the next section) before you commit.

## 2. Add a new mart

Example: `dim_countries`, one row per country from the `countries` seed, in the `shared` domain. Four steps.

**1. The SQL**, `dbt/models/marts/shared/dim_countries.sql`. The file name is the table name. Read other models through `ref()`, never by schema and table name:

```sql
-- The countries the shop sells to, from the countries seed.
select
    code::text as country_code,
    name::text as country_name,
    region::text as region,
    is_eu::boolean as is_eu
from {{ ref('countries') }}
```

A model that needs lookups or joins starts with one CTE per input (`with customers as (select * from {{ ref('stg_shop__customers') }}), ...`), like the existing marts.

**2. The YAML**, appended to `dbt/models/marts/shared/_shared__models.yml`:

```yaml
  - name: dim_countries
    description: Countries the shop sells to, one row per ISO country code, with region and EU membership.
    config:
      meta:
        domain: shared
        grain: one row per country
        primary_key: country_code
    columns:
      - name: country_code
        description: ISO 3166-1 alpha-2 country code. Primary key.
        data_type: text
        constraints:
          - type: not_null
          - type: primary_key
        data_tests: [unique, not_null]
      - name: country_name
        description: English name of the country.
        data_type: text
      - name: region
        description: Sales region the country belongs to.
        data_type: text
      - name: is_eu
        description: True for European Union member states.
        data_type: boolean
```

- `meta.domain`, `meta.grain` and `meta.primary_key` are required on every mart by convention (`primary_key` is a column name or a list of names): the agent reads them, and no check enforces them, so review them yourself.
- `constraints: primary_key` makes Postgres enforce the key and create its index; `unique` and `not_null` as data tests also report violations as test failures, which is what you see in the dbt output.
- For a fact table add an index on each column you will join or filter on, in the model's SQL file:

  ```sql
  {{ config(indexes=[{'columns': ['order_id']}]) }}
  ```

  `fct_orders` indexes `order_date` and `customer_id`; `fct_order_items` indexes `order_id` and `variant_id`; `fct_order_discounts` indexes `order_id` and `promotion_id`.
- Text columns people search by name get a trigram index (section 4).

**3. Grants.** Nothing to do. The `+grants` setting in `dbt_project.yml` gives `sherpa_agent` `SELECT` on every mart table, and the `on-run-end` hook `grant_agent_access` grants `USAGE` on the `marts` schema and `SELECT` on all of its tables on prod and eval. The agent can read the new table after the next `make build-prod`.

**4. Build, then check the docs.**

```bash
dbt build --select dim_countries
```

```
22:29:08  1 of 3 OK created sql table model dev_marts.dim_countries ...................... [INSERT 0 8 in 0.32s]
22:29:08  2 of 3 PASS not_null_dim_countries_country_code ................................ [PASS in 0.09s]
22:29:08  3 of 3 PASS unique_dim_countries_country_code .................................. [PASS in 0.08s]
22:29:08  Done. PASS=5 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=5
```

(The 5 include the two `on-run` hooks.)

`make docs-check` regenerates the docs and fails when any model or column has no description. Without the `region` description above it reports:

```
Missing descriptions:
  dim_countries.region
```

It also catches a column that exists in Postgres but not in the YAML. With the description added it prints nothing and exits 0.

Finally add the mart to the list in `dbt/models/overview.md`, add its row to the spec's §6.5, and add a test for any rule you rely on. The agent access test (`tests/dbt/test_agent_access.py`) lists every mart in its `MARTS` tuple: add the new table there.

## 3. Change a metric definition

A metric is defined in four places that must agree. Change them in this order:

1. **The doc block** in `dbt/models/marts/_metrics.md`, for example `metric_refunded_revenue`. A *doc block* is a named piece of Markdown (`{% docs metric_refunded_revenue %} ... {% enddocs %}`) that YAML descriptions reuse with `{{ doc('metric_refunded_revenue') }}`. One edit changes the description of every column that reuses it, in the docs, in the Postgres comments and in the manifest the agent reads. This is how every consumer sees the same definition.
2. **The calculation.** Where the rule is in a macro, change the macro, and every model that calls it follows: `revenue_excluding_tax(amount)` in `dbt/macros/revenue_excluding_tax.sql` is used by `fct_refunds` and `int_orders__refunds_summarized`; `customer_segment(order_count, days_since_last_order)` in `dbt/macros/customer_segment.sql` is used by `dim_customers`. A rule in a model's SQL (such as net revenue in `fct_orders`) is changed there. Project variables such as `tax_rate` live in `dbt_project.yml`.
3. **The tests that pin it.** Singular tests in `dbt/tests/` state the rule with fixed inputs and expected outputs, so a change in the rule fails them until you update them on purpose:
   - `assert_revenue_excluding_tax`: the macro on 120.00, 64.80, 1.00, 0.01 and 0.00 EUR.
   - `assert_customer_segment_boundaries`: the segment at 0 orders and at 90/91 and 180/181 days.
   - `assert_cancelled_orders_have_no_outcomes`: a cancelled order has 0 net revenue, cogs and margin.
   - `assert_line_refunds_within_order_refunds`, `assert_captured_payments_equal_order_total` and the `column_sums_match` tests in the YAML: totals that two marts must agree on.
4. **The spec** (§6.5 and §6.6), which says what the numbers mean.

To see the tests do their job, run the macro's test with a different tax rate (a project variable is overridden on the command line with `--vars`):

```bash
dbt test --select assert_revenue_excluding_tax --vars '{tax_rate: 0.25}'
```

```
21:11:34  1 of 1 FAIL 3 assert_revenue_excluding_tax ..................................... [FAIL 3 in 0.12s]
21:11:35    Got 3 results, configured to fail if != 0
```

Three of the five pinned cases do not match the new rate, so the change cannot go unnoticed. After changing a definition, run `make build` (all tests), then `make build-prod` and `make docs-prod` so the prod artifacts carry the new text. The marts the agent reads change with the next prod build, not before.

## 4. Add a trigram index

A trigram index speeds up fuzzy text search (`column % 'text'`, `'text' <% column`) on a text column. Add one when people look rows up by that column's value, such as product names. The macro `create_trigram_index(column)` in `dbt/macros/create_trigram_index.sql` creates a GIN index on the column; add a line to the model's `post_hook` list (a *hook* is SQL dbt runs after the model builds; the quotes make it a string, so `{{ this }}` is resolved for each run):

```sql
{{
    config(
        post_hook=[
            "{{ create_trigram_index('product_name') }}",
            "{{ create_trigram_index('brand') }}",
            "{{ create_trigram_index('category_l1') }}",
            "{{ create_trigram_index('category_l2') }}",
            "{{ create_trigram_index('category_l3') }}",
            "{{ create_trigram_index('color') }}",
        ]
    )
}}
```

The extension itself (`pg_trgm`, in the `extensions` schema) is installed by the `on-run-start` hook. Rebuild and check that the planner uses the index (`set enable_seqscan=off` stops Postgres preferring a full scan on this small table, so the plan shows the index is usable):

```bash
dbt build --select dim_products
docker compose exec postgres psql -U sherpa_admin -d sherpa -c "select indexname from pg_indexes where schemaname='dev_marts' and tablename='dim_products' and indexname like '%color%'"
```

```
        indexname
-------------------------
 dim_products_color_trgm
```

```sql
set enable_seqscan=off; explain select distinct color from dev_marts.dim_products where color % 'blck';
```

```
   ->  Bitmap Heap Scan on dim_products  (cost=26.99..59.95 rows=157 width=5)
         Recheck Cond: (color % 'blck'::text)
         ->  Bitmap Index Scan on dim_products_color_trgm  (cost=0.00..26.95 rows=157 width=0)
               Index Cond: (color % 'blck'::text)
```

A table is rebuilt from scratch on every run, so the hook creates the index again each time. Index names are unique per schema, and while the post-hooks run, dbt still keeps the previous build's table as `dim_products__dbt_backup`, holding an index with the same name; dbt drops that backup, with its indexes, after the hooks. So the macro first runs `drop index if exists <schema>.<index name>`, which removes the backup's copy, and then `create index` on the new table. (`create index if not exists` would find the backup's index, skip, and leave the new table without one once the backup is gone.) To check that an index survives a rebuild, run the build twice and query `pg_indexes` after each run; the `warehouse` tests check the five `dim_products` indexes on the prod and eval databases.

## 5. Write a custom generic test

dbt's built-in tests (`unique`, `not_null`, ...) check one column. `column_sums_match` checks that two models add up to the same total per group. It is a *generic test*: a Jinja `{% test %}` block in `dbt/tests/generic/column_sums_match.sql` that dbt makes available to every YAML file by its name:

```sql
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
```

The rules of a generic test:

- The test returns the **failing rows**; zero rows is a pass. Here, one row per group whose two sums differ. The full outer join and the `coalesce` make a group missing on one side count as 0.
- `model` is filled in by dbt with the model the test is attached to; `column_name` is the column of a column-level test (a model-level test omits it). The other arguments come from the YAML. It is written as `{{ group_by }}` in SQL, so it is a column name, not a value.
- Arguments go under `arguments:` in the YAML. A model-level test is attached to the model, like this one on `fct_order_items`, whose lines must add up to the order:

  ```yaml
      data_tests:
        - column_sums_match:
            arguments:
              column_name: gross_amount
              compare_model: ref('fct_orders')
              compare_column: gross_revenue
              group_by: order_id
  ```

The same test is used seven times (lines against orders for gross amount, discount, cost and returned cost; discount rows of `fct_order_discounts` against orders; refunds against orders; allocated discounts against the discount rows). Run all of them:

```bash
dbt test --select "test_name:column_sums_match" --target prod --target-path target-prod
```

```
22:55:30  7 of 7 PASS column_sums_match_int_order_items__discount_allocated_allocated_discount__amount__ref_stg_shop__order_discounts___order_id  [PASS in 0.23s]
22:55:30  Done. PASS=9 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=9
```

(The 9 include the two `on-run` hooks.) `dbt ls --select "test_name:column_sums_match" --resource-type test` lists them. [Building the warehouse](../basic/building-the-warehouse.md#reading-a-column_sums_match-failure) shows what a failure looks like.

Write a generic test when the same rule applies to several models or columns. A rule about one model with fixed values (the segment boundaries, the tax maths) is a singular test: a plain `SELECT` returning the failing rows in `dbt/tests/<name>.sql`, with the cases written out in the file. Add either kind with a comment that says what a returned row means.

## 6. Ephemeral, view or table for an intermediate model

Intermediate models (`dbt/models/intermediate/`) default to `ephemeral` (`dbt_project.yml`). Override the default in the model's own file with `{{ config(materialized='view') }}` only when the reason below applies:

| Materialization | What dbt builds | Use it when |
|---|---|---|
| `ephemeral` | Nothing. The SQL is pasted into every model that uses it as a CTE | The default: a step used by one or two marts, cheap to recompute. Cannot be queried or tested on its own |
| `view` | A view in the `intermediate` schema | You want to query it while debugging, put tests on it, or unit-test it. The query reruns on every read |
| `table` | A table, rebuilt on every run | The query is expensive and several models read it |

The current choices:

- `int_events__sessionized` is a **table**: it numbers web sessions with window functions over every event, and `fct_orders` and `dim_customers` both read it.
- `int_order_items__discount_allocated`, `int_order_items__returns_summarized` and `int_orders__refunds_summarized` are **views**: each has tests on its own result (`unique` on its key, an `expression_is_true` that the allocated discount does not exceed the line's gross amount).
- The other intermediate models (`int_order_items__costed`, `int_orders__payments_summarized`, `int_orders__status_pivoted`, `int_identity__lead_to_customer`) are **ephemeral**.

Tests attach to a model that exists in Postgres, which is the usual reason for choosing a view over ephemeral. `dbt ls` lists the current choices:

```bash
dbt --quiet ls --select intermediate --resource-type model --output json --output-keys name config.materialized
```

```
{"name": "int_events__sessionized", "config.materialized": "table"}
{"name": "int_identity__lead_to_customer", "config.materialized": "ephemeral"}
{"name": "int_order_items__costed", "config.materialized": "ephemeral"}
{"name": "int_order_items__discount_allocated", "config.materialized": "view"}
{"name": "int_order_items__returns_summarized", "config.materialized": "view"}
{"name": "int_orders__payments_summarized", "config.materialized": "ephemeral"}
{"name": "int_orders__refunds_summarized", "config.materialized": "view"}
{"name": "int_orders__status_pivoted", "config.materialized": "ephemeral"}
```

Intermediate models are not exposed to consumers: the `sherpa_agent` role cannot read the `intermediate` schema, so a model there is an implementation detail you can rename or restructure freely, as long as the marts keep the same shape.

## Next

- [Querying the marts](../basic/querying-the-marts.md): what each mart answers and the traps in using it.
- [Building the warehouse](../basic/building-the-warehouse.md): run, select, preview and debug.
