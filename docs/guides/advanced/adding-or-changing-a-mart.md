# Adding or changing a mart

How to change what the marts expose: add a column, add a mart, change a metric, add a trigram index, write a custom test, and choose how an intermediate model is materialized. Each section lists the files to touch, in order, and the tests that prove the change. Run the dbt commands from `data-platform/dbt/` with the usual prefix (`DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt`, shortened to `dbt`; see [Building the warehouse](../basic/building-the-warehouse.md#running-dbt-directly)).

## Before you start

The marts' columns, grain and metric definitions are in the [data platform spec](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) (§6.5, §6.6). Changing them is a spec change: agree it with the owner and update the spec in the same branch. The agent relies on the marts as §11 describes them, so a rename or a removal breaks it.

Everything under `data-platform/dbt/models/marts/` inherits from `dbt_project.yml`: `materialized: table`, `contract: enforced` (dbt checks the model's columns and types against the YAML before building), `persist_docs` (descriptions become Postgres comments), an `analyze` post-hook and `SELECT` grants for the agent and UI roles. The folder is `marts/<domain>/`, with one `_<domain>__models.yml` per folder and `meta.domain` matching the folder. Metric definitions live in `marts/_metrics.md`.

## 1. Add a column to a mart

Example: `dim_products.current_margin`, list price minus cost price. Three steps.

1. **The SQL**, `models/marts/products/dim_products.sql`: add the column to the last CTE (`final`) with an explicit cast to the type you will declare.

   ```sql
   (variants.list_price - variants.cost_price)::numeric(12, 2) as current_margin,
   ```

2. **The YAML**, `models/marts/products/_products__models.yml`, in the same position:

   ```yaml
         - name: current_margin
           description: Current list price minus current cost price of the variant in EUR.
           data_type: numeric(12,2)
           config:
             meta:
               synonyms: [unit margin]
           data_tests: [not_null]
   ```

   `data_type` and `description` are mandatory. `meta.synonyms` helps the agent match questions to the column; add `meta.pii: true` for personal data. Reuse a metric's doc block with `{{ doc('metric_net_revenue') }}`.

3. **Build and test:**

   ```bash
   dbt build --select dim_products
   docker compose exec postgres psql -U sherpa_admin -d sherpa -c '\d dev_marts.dim_products' | grep current_margin
   ```

   `--select` also runs tests on other models that reference this one. The YAML decides the real column type, so check it in Postgres.

A new source column needs the source and staging models first: see [Extending the source data model](extending-the-source-data-model.md). A column another mart reads must exist upstream first; `dbt build --select fct_orders+` rebuilds a model and everything downstream. Finish with `make build` and `make lint` (its docs coverage check fails on a model or column without a description).

### What a contract error looks like

```
  | column_name  | definition_type | contract_type | mismatch_reason     |
  | ------------ | --------------- | ------------- | ------------------- |
  | double_price | DECIMAL         |               | missing in contract |
```

`definition_type` is what the SQL produces, `contract_type` what the YAML says. `missing in contract` means a column is in the SQL but not the YAML; `data type mismatch` means the two types differ. A column without `data_type` gets "Contracted models require data_type to be defined". Fix the cast or the YAML, whichever is wrong. More in [Reading a contract failure](../basic/building-the-warehouse.md#reading-a-contract-failure).

## 2. Add a new mart

Example: `dim_countries`, one row per country from the `countries` seed, domain `shared`.

1. **The SQL**, `models/marts/shared/dim_countries.sql` (the file name is the table name). Read other models through `ref()`, with one CTE per input like the existing marts.

   ```sql
   select
       code::text as country_code,
       name::text as country_name,
       region::text as region,
       is_eu::boolean as is_eu
   from {{ ref('countries') }}
   ```

2. **The YAML**, appended to `models/marts/shared/_shared__models.yml`: a description, `config.meta` with `domain`, `grain` and `primary_key` (a column name or a list; the agent reads them and nothing enforces them, so review them yourself), and every column with `description` and `data_type`. Put `constraints: [not_null, primary_key]` and `data_tests: [unique, not_null]` on the key: the constraint makes Postgres enforce it, the tests report violations.
3. **Indexes.** For a fact table, add an index per join or filter column in the SQL file: `{{ config(indexes=[{'columns': ['order_id']}]) }}`. Text columns searched by name get a trigram index (section 4).
4. **Grants.** Nothing to do: the `+grants` setting and the `grant_read_access` hook cover every mart table (see [Building the warehouse](../basic/building-the-warehouse.md)). The agent reads the table after the next `make build-prod`.
5. **Registry.** Add the mart to `models/overview.md`, its row to the spec's §6.5, and the table to the `MARTS` tuple in `data-platform/tests/dbt/test_reader_access.py`.
6. **Prove it:**

   ```bash
   dbt build --select dim_countries
   ```

   then `make lint` from the repository root, which fails with `Missing descriptions: dim_countries.region` if a description is missing. Add a test for any rule you rely on.

## 3. Change a metric definition

A metric is defined in four places that must agree. Change them in this order:

1. **The doc block** in `models/marts/_metrics.md` (`{% docs metric_refunded_revenue %} ... {% enddocs %}`). Columns reuse it with `{{ doc(...) }}`, so one edit changes the docs, the Postgres comments and the manifest the agent reads.
2. **The calculation.** A macro change reaches every caller: `revenue_excluding_tax` (`macros/revenue_excluding_tax.sql`; `fct_refunds`, `int_orders__refunds_summarized`) and `customer_segment` (`macros/customer_segment.sql`; `dim_customers`). A rule in a model's SQL (net revenue in `fct_orders`) is changed there. Variables such as `tax_rate` live in `dbt_project.yml`.
3. **The tests that pin it**, which fail until you update them on purpose: the unit tests in the model's YAML (`dim_customers_segments_at_the_90_and_180_day_boundaries`: 0 orders; 90/91 and 180/181 days; `int_orders__refunds_summarized_untaxes_returns_and_goodwill`) and the singular tests in `data-platform/dbt/tests/singular/`: `assert_cancelled_orders_have_no_outcomes`, `assert_line_refunds_within_order_refunds`, `assert_captured_payments_equal_order_total`, plus the `column_sums_match` tests in the YAML.
4. **The spec** (§6.5, §6.6).

To see the pinning work, override the variable: `dbt test --select test_type:unit --vars '{tax_rate: 0.25}'` fails the refunds unit test with an actual-versus-expected diff. After the change run `make build`, then `make build-prod`; the agent sees the new definition only after the prod build.

## 4. Add a trigram index

A trigram index speeds up fuzzy search (`column % 'text'`) on a text column people look up by value, such as product names. Add a line to the model's `post_hook` list in `models/marts/products/dim_products.sql`:

```sql
"{{ create_trigram_index('color') }}",
```

The macro `create_trigram_index` (`macros/create_trigram_index.sql`) creates a GIN index named `<table>_<column>_trgm`; the `on-run-start` hook installs `pg_trgm`. The macro first drops any index of that name because dbt keeps the previous table as `dim_products__dbt_backup` while hooks run, holding an index with the same name. Prove it:

```bash
dbt build --select dim_products
docker compose exec postgres psql -U sherpa_admin -d sherpa -c "select indexname from pg_indexes where schemaname='dev_marts' and tablename='dim_products' and indexname like '%color%'"
```

Build twice to check the index survives a rebuild. In `psql`, `set enable_seqscan=off; explain select distinct color from dev_marts.dim_products where color % 'blck';` should show a Bitmap Index Scan on the new index. The `warehouse` tests check the `dim_products` indexes on prod and eval (`uv run --env-file .env pytest -m warehouse`).

## 5. Write a custom generic test

A generic test is a Jinja `{% test %}` block under `data-platform/dbt/tests/generic/` that every YAML file can use by name. `column_sums_match.sql` there checks that two models add up to the same total per group. Rules:

- The test returns the **failing rows**; zero rows is a pass. A full outer join with `coalesce(..., 0)` makes a group missing on one side count as 0.
- dbt fills in `model` (and `column_name` for a column-level test); other arguments come from the YAML under `arguments:`. They are pasted into SQL, so they are column names, not values.
- A model-level use, on `fct_order_items`:

  ```yaml
      data_tests:
        - column_sums_match:
            arguments:
              column_name: gross_amount
              compare_model: ref('fct_orders')
              compare_column: gross_revenue
              group_by: order_id
  ```

Run every use with `dbt test --select "test_name:column_sums_match" --target prod --target-path target-prod`; [Building the warehouse](../basic/building-the-warehouse.md#reading-a-column_sums_match-failure) shows a failure. Write a generic test when the rule applies to several models; a rule about one model with fixed cases is a singular test, a plain `SELECT` of failing rows in `data-platform/dbt/tests/singular/<name>.sql`. Comment what a returned row means.

## 6. Ephemeral, view or table for an intermediate model

Intermediate models (`models/intermediate/`) default to `ephemeral`. Override with `{{ config(materialized='view') }}` in the model's file only when a reason below applies:

| Materialization | What dbt builds | Use it when |
|---|---|---|
| `ephemeral` | Nothing: the SQL is pasted into each user as a CTE | The default; cheap step used by one or two marts. Cannot be queried or tested alone |
| `view` | A view in the `intermediate` schema | You need to query it, test it or unit-test it |
| `table` | A table, rebuilt each run | The query is expensive and several models read it |

Current choices: `int_events__sessionized` is a table (window functions over every event, read by `fct_orders` and `dim_customers`); `int_order_items__discount_allocated`, `int_order_items__returns_summarized` and `int_orders__refunds_summarized` are views because they carry their own tests; the rest are ephemeral. List them with:

```bash
dbt --quiet ls --select intermediate --resource-type model --output json --output-keys name config.materialized
```

The agent role cannot read the `intermediate` schema, so these models can be renamed or restructured freely as long as the marts keep their shape.

## Common errors

| Symptom | Fix |
|---|---|
| `missing in contract` | Add the column to the model's YAML, with `data_type` |
| `data type mismatch` | Align the SQL cast and the YAML `data_type` |
| `Contracted models require data_type` | Add `data_type` to the named column |
| `make lint` reports `Missing descriptions` | Add a `description` to the model or column |
| Agent cannot see the new table | Run `make build-prod`; the grant hook runs with every build |
| Trigram index missing after a rebuild | Use `create_trigram_index` in `post_hook`, not a hand-written `create index if not exists` |
| Singular test fails after a metric change | Update its expected values on purpose, then the spec |

## Next

- [Querying the marts](../basic/querying-the-marts.md): what each mart answers and the traps in using it.
- [Building the warehouse](../basic/building-the-warehouse.md): run, select, preview and debug.
