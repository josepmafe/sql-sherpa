# Adding or changing a mart

How to change what the marts expose: add a column, add a mart, change a metric, add a trigram index, write a custom test, and choose how an intermediate model is materialized. Each section lists the files to touch, in order, and the tests that prove the change. Run the dbt commands from `data-platform/dbt/` with the usual prefix (`DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt`, shortened to `dbt`; see [Building the warehouse](../basic/building-the-warehouse.md#running-dbt-directly)).

## Before you start

The marts' columns, grain and metric definitions are in the [data platform spec](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) (§6.5, §6.6). Changing them is a spec change: agree it with the owner and update the spec in the same branch. The agent relies on the marts as §11 describes them, so a rename or a removal breaks it.

Everything under `data-platform/dbt/models/marts/` inherits from `dbt_project.yml`: `materialized: table`, `contract: enforced` (dbt checks the model's columns and types against the YAML before building), `persist_docs` (descriptions become Postgres comments), an `analyze` post-hook and `SELECT` grants for the agent and UI roles, which `grant_read_access` applies per relation. The folder is `marts/<domain>/`, with one `_<domain>__models.yml` per folder and `meta.domain` matching the folder. The folder also sets the model's **group** (its owning domain, declared in `models/_groups.yml`) and `access: public`: a visible mart is public, so any model may `ref` it. A model with `access: private` can be `ref`ed only from its own group, which `dbt parse` enforces. Metric definitions live in `marts/_metrics.md`.

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

   `data_type` and `description` are mandatory. `meta.synonyms` helps the agent match questions to the column; for personal data see [section 8](#8-add-a-mart-with-pii). Reuse a metric's doc block with `{{ doc('metric_net_revenue') }}`.

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
4. **Group and access.** Nothing to do in an existing folder: `dbt_project.yml` gives it the folder's group and `public`. A new domain folder needs `+group: <domain>` under `marts:` in `dbt_project.yml` and a `groups:` entry in `models/_groups.yml`.
5. **Grants.** Nothing to do: the `+grants` setting and the `grant_read_access` hook, which grants per relation, cover every visible mart (see [Building the warehouse](../basic/building-the-warehouse.md#hooks-and-grants)). The agent reads the table after the next `make build-prod`.
6. **Registry.** Add the mart to the `sherpa_agent` exposure (`models/marts/_exposures.yml`, `depends_on`), to `models/overview.md`, its row to the spec's §6.5, and the table to the `MARTS` tuple in `data-platform/tests/dbt/test_reader_access.py`. `assert_agent_exposure_lists_every_visible_mart` fails the build with `(unique_id, problem)` rows (`not in sherpa_agent`, `visible but not public`, `unmasked but not private`) when the exposure and the visible marts differ.
7. **Prove it:**

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

**Tag a test `distribution`** (`tags: [distribution]` in the YAML, `config(tags=['distribution'])` in a singular test) only when it checks the generator's random draws against a full-year target, such as a row-count or mean expectation or the Pareto check. Nothing else gets the tag: CI skips tagged tests because its 30-day simulation cannot meet those targets ([CI](ci.md)).

## 6. Ephemeral, view or table for an intermediate model

Intermediate models (`models/intermediate/`) default to `ephemeral`. Override with `{{ config(materialized='view') }}` in the model's file only when a reason below applies:

| Materialization | What dbt builds | Use it when |
|---|---|---|
| `ephemeral` | Nothing: the SQL is pasted into each user as a CTE | The default; cheap step used by one or two marts. Cannot be queried or tested alone |
| `view` | A view in the `intermediate` schema | You need to query it, test it or unit-test it |
| `table` | A table, rebuilt each run | The query is expensive and several models read it |

Current choices: `int_events__sessionized` (`delete+insert`) and `int_identity__anonymous_to_customer` (`merge`) are incremental tables, so a build recomputes only what the new events touch; `int_order_items__costed`, `int_order_items__discount_allocated`, `int_order_items__returns_summarized` and `int_orders__refunds_summarized` are views because they carry their own tests; the rest are ephemeral. List them with:

```bash
dbt --quiet ls --select intermediate --resource-type model --output json --output-keys name config.materialized
```

The agent role cannot read the `intermediate` schema, so these models can be renamed or restructured freely as long as the marts keep their shape. An intermediate model only one group uses is `access: private` to it (`orders` is private to `sales`); a new one takes its folder's group and access from `dbt_project.yml`.

## 7. Change an incremental model

Three models build incrementally, each with the strategy its job needs ([spec §6.2 to §6.4](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)). All three set `on_schema_change: fail`.

| Model | Strategy | Why | Watermark |
|---|---|---|---|
| `stg_events__events` | `microbatch` on `received_at` | Events arrive late and are the biggest table; a run reprocesses today and the 7 days before | None stored: the run date, `lookback=7`, and `begin` from `SHERPA_SIMULATION_START_DATE` |
| `int_identity__anonymous_to_customer` | `merge` on `anonymous_id` | One row per device whose values change when it is identified again | `max(last_received_at)` of the table itself |
| `int_events__sessionized` | `delete+insert` on `session_date` | A late event rewrites a whole session, sometimes across midnight, so whole days are replaced | `max(max_received_at)` of the table itself |

The two table watermarks make a run independent of the date it runs: a missed day is caught up by the next run.

**Change the logic.**

1. Edit the `.sql`. Keep the `{% if is_incremental() %}` branch and the full-build branch equal in meaning.
2. Unit-test the incremental branch with `overrides: macros: {is_incremental: true}`. Give `this` (the table's current content) and any ephemeral input as `format: sql` (dbt can only give an ephemeral model as SQL; see `int_events__sessionized_puts_late_event_in_existing_session` in `models/intermediate/events/_events__models.yml`). Also test the empty-table case (`int_identity__anon_to_customer_reads_all_when_table_empty`): `is_incremental()` is true on the first run after the table exists but is empty.
3. Rebuild with `dbt build --select stg_events__events+` (the staging guard test rides with it; never build downstream alone). A change to the model SQL, `channel_mapping` or `session_timeout_minutes` (or to the map's logic, for `int_identity__anonymous_to_customer+`) needs `--full-refresh --select int_events__sessionized+` on each target, run only after the guard passes (or select `stg_events__events int_events__sessionized+`); only changes affecting new rows alone build incrementally. The unit tests run first.
4. Prove the incremental result equals a full rebuild: run a full refresh in prod, then the analysis (below).

**When the columns change.** `on_schema_change: fail` stops an incremental run whose columns differ from the table's, so a new column needs `dbt build --full-refresh --select <model>+`. Do not use `append_new_columns`: it leaves the old rows null and the incremental result differs from a full rebuild. `--full-refresh` on `stg_events__events` rebuilds every daily batch from `begin`, about two minutes on a year of data.

**Add a column to a snapshot.** `snap_product_variants` (`snapshots/_snapshots.yml`) captures `raw_shop.product_variants` each time `dbt snapshot` runs, and `--full-refresh` does not touch it. A new raw column means `make reset && make backfill` ([Extending the source data model](extending-the-source-data-model.md)), which fills it for every version. Add it to the YAML description too.

**The proof.** After a full refresh in prod:

```bash
dbt show --select fct_sessions_incremental_vs_full_refresh --vars '{compare_schema: marts}'
```

Run on the dev target (the default), it compares `dev_marts.fct_sessions`, which `make build` has updated incrementally, with the prod table that the full refresh rebuilt; zero rows means identical. The analysis requires `compare_schema` only when it is the selected node; selecting it by `--selector` or resource type skips that check and fails on a placeholder schema.

## 8. Add a mart with PII

Example: a mart `fct_leads` with an `email` column. A mart with personal data is a pair ([spec §6.5](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)), so the readers get hashes and never the values:

1. **The table**, `models/marts/<domain>/fct_leads_unmasked.sql`: the model of section 2, named `<mart>_unmasked`, with `grants: {select: []}` and `access: private` under `config:` in its YAML entry (never `+select`, which would append to the project-level grants and let the readers read the table), and `meta.pii: true` on each personal column, as in `dim_customers_unmasked`. Private keeps it in its group: only models of that group may `ref` it.
2. **The view**, `models/marts/<domain>/fct_leads.sql`: `{{ config(materialized='view') }}`, the table's columns in the same order, each personal one as `{{ mask_pii('email') }} as email`, from `{{ ref('fct_leads_unmasked') }}`. Leave out a personal column that only invites wrong answers (`dim_customers` omits the names). Its YAML entry repeats the columns with `meta.masked: true` on exactly the `meta.pii` ones it keeps; their description says it is a 32-character hash (copy the `email` description of `dim_customers`). Do not make it `security_invoker`: the view reads `ops.pii_salt` with the owner's rights.
3. **Who reads the table.** Only the sibling view refs `fct_leads_unmasked`; every other model refs `fct_leads`. `assert_pii_marts_are_masked_views` has an arm for it and returns `(model, 'reads fct_leads_unmasked; ref fct_leads instead')`, also for another `_unmasked` model. A singular test that refs an `_unmasked` table (and returns no value of its PII columns) takes the table's group: `{{ config(group='customers') }}` on its first line, or `dbt parse` refuses it as private to that group. The pair-registration test itself reads the graph, so add two lines for the pair: `-- depends_on: {{ ref('fct_leads') }}` and `-- depends_on: {{ ref('fct_leads_unmasked') }}`.
4. **Registry.** `fct_leads` goes in the `sherpa_agent` exposure and in `MARTS` and `fct_leads_unmasked` in `UNMASKED` in `data-platform/tests/dbt/test_reader_access.py`; the new columns go in the expected set of `test_masked_columns_are_the_specs` (`agent/mcp-server/tests/test_catalog.py`); the spec's §6.5 gets the pair.
5. **Prove it:**

   ```bash
   dbt build --select fct_leads_unmasked+
   ```

   The `+` is required: building the table alone drops its view. Then `make build-prod` and, from the repository root, `uv run --env-file .env pytest data-platform/tests/dbt/test_reader_access.py -m warehouse` (`TestMaskedMarts`: no reader privilege on the table or the salt, only hashes through the view) and `uv run --env-file .env pytest agent/mcp-server/tests/test_catalog.py -m warehouse` (the catalog marks exactly the view's masked columns as PII, and refuses a visible mart that still carries `meta.pii`). The singular test runs in every build and fails with `(model, problem)` rows, never a value.

## Common errors

| Symptom | Fix |
|---|---|
| `missing in contract` | Add the column to the model's YAML, with `data_type` |
| `data type mismatch` | Align the SQL cast and the YAML `data_type` |
| `Contracted models require data_type` | Add `data_type` to the named column |
| `make lint` reports `Missing descriptions` | Add a `description` to the model or column |
| Agent cannot see the new table | Run `make build-prod`; the grant hook runs with every build |
| `assert_agent_exposure_lists_every_visible_mart` returns `not in sherpa_agent` | Add `ref('<mart>')` to the exposure in `models/marts/_exposures.yml` |
| `Parsing Error ... which is not allowed because the referenced node is private to the 'customers' group` | A model of another group refs a private model: ref the visible mart, or put the model in the group (a singular test: `config(group=...)`) |
| `assert_pii_marts_are_masked_views` returns `reads <mart>_unmasked; ref <mart> instead` | Only the sibling view may read the table: ref the view |
| `assert_pii_marts_are_masked_views` fails, or a reader gets `relation ... does not exist` for a PII mart | A `meta.pii` model is not a table named `*_unmasked`, the view is missing or differs in columns, or the table was built without its view: `dbt build --select <mart>_unmasked+` |
| Trigram index missing after a rebuild | Use `create_trigram_index` in `post_hook`, not a hand-written `create index if not exists` |
| Singular test fails after a metric change | Update its expected values on purpose, then the spec |
| `The source and target schemas on this incremental model are out of sync!` | Columns changed, or another checkout built the model with other code: `dbt build --full-refresh --select <model>+` on that target (section 7) |

## Next

- [Querying the marts](../basic/querying-the-marts.md): what each mart answers and the traps in using it.
- [Building the warehouse](../basic/building-the-warehouse.md): run, select, preview and debug.
