# Building the warehouse

dbt turns the raw schemas the generator writes into tested, documented models. This guide shows how to run it, how to look at what it built, and what to do when a test fails. It assumes Postgres is up and holds data ([Using the generator](generator-cli.md)).

A few dbt terms first:

- A **model** is a `SELECT` in a `.sql` file under `data-platform/dbt/models/`. dbt wraps it in `CREATE VIEW` or `CREATE TABLE` (its *materialization*) and runs the models in dependency order.
- A **source** declares a table dbt did not build, here the generator's `raw_*` tables. Models read it with `{{ source('shop', 'orders') }}`, other models with `{{ ref('stg_shop__orders') }}`. These two functions give dbt the dependency graph (the *DAG*).
- A **seed** is a small CSV in `data-platform/dbt/seeds/` that dbt loads as a table, such as `countries`.
- A **data test** is a query that returns the rows breaking a rule (`unique`, `not_null`, `accepted_values`, `relationships`, ...). Zero rows means it passes. A **singular test** is a plain SQL file in `data-platform/dbt/tests/` that returns the failing rows (for example `assert_customer_segment_boundaries` and `assert_fct_orders_keeps_every_order`). A **generic test** is a reusable test written once as a Jinja `{% test %}` block and applied from YAML; `data-platform/dbt/tests/generic/column_sums_match.sql` is ours.
- A **contract** is a promise in the YAML that a model has exactly the listed columns with the listed types. dbt checks it before it builds the table. The marts enforce one.
- A **hook** is SQL dbt runs around a build: `on-run-start` and `on-run-end` once per invocation, `post-hook` after one model.
- A **target** is one named connection in `data-platform/dbt/profiles.yml`: `dev` (the default), `prod` and `eval`. Credentials come from `.env` through `env_var()`.
- `dbt run-operation <macro>` runs one macro on its own, outside a build (`make grant-read` uses it).
- **`persist_docs`** is a config that copies the YAML descriptions into Postgres comments on the tables and columns, so a database client can read them.
- **`grants`** is a config that makes dbt `GRANT` privileges on each table it builds (here `SELECT` to `sherpa_agent` and `sherpa_ui` on the marts).

The project has the seeds, one staging view per raw table, an intermediate layer and the nine marts. Outputs below are real and come from the 365-day dataset unless stated; timings vary by machine.

## Dev and prod

The same project builds into different schemas depending on the target (the `generate_schema_name` macro in `data-platform/dbt/macros/`):

| | `dev` (default) | `prod` |
|---|---|---|
| Seeds | `dev_seeds` | `seeds` |
| Staging views | `dev_staging` | `staging` |
| Intermediate | `dev_intermediate` | `intermediate` |
| Marts | `dev_marts` | `marts` |
| Agent access | none | `sherpa_agent` reads `marts` |
| Artifacts (`manifest.json`, `catalog.json`, ...) | `data-platform/dbt/target/` | `data-platform/dbt/target-prod/` |
| Make targets | `make build`, `make docs` | `make build-prod`, `make docs-prod` |

Use `dev` while you work; `prod` is what consumers read. Prod commands always pass `--target-path target-prod`, so a dev run never overwrites the prod artifacts. A third target, `eval`, builds the frozen evaluation database (see [The frozen eval database](#the-frozen-eval-database)). After `make build` and `make build-prod` the database has both sets next to the raw schemas:

```
       List of schemas
       Name       |       Owner
------------------+-------------------
 dev_intermediate | sherpa_admin
 dev_marts        | sherpa_admin
 dev_seeds        | sherpa_admin
 dev_staging      | sherpa_admin
 extensions       | sherpa_admin
 intermediate     | sherpa_admin
 marts            | sherpa_admin
 public           | pg_database_owner
 raw_crm          | sherpa_admin
 raw_events       | sherpa_admin
 raw_marketing    | sherpa_admin
 raw_meta         | sherpa_admin
 raw_shop         | sherpa_admin
 seeds            | sherpa_admin
 staging          | sherpa_admin
```

(`docker compose exec postgres psql -U sherpa_admin -d sherpa -c '\dn'`.) `extensions` holds the `pg_trgm` extension for fuzzy text search. The `intermediate` schemas exist but hold only the four intermediate models that are views or tables.

## The layers

Data flows through three layers, each in its own schema:

- **Staging** (`models/staging/`): one view per raw table; renames, casts and cleans, no business logic.
- **Intermediate** (`models/intermediate/`, `int_<entity>__<verb>`): joins and calculations that several marts need, such as allocating an order's discount over its lines (`int_order_items__discount_allocated`) or numbering web sessions (`int_events__sessionized`). Consumers never read them. Most are *ephemeral*: dbt pastes the SQL into the models that use them as a CTE (a `WITH` block) and creates nothing in Postgres. The exceptions are `int_events__sessionized` (a table) and `int_order_items__discount_allocated`, `int_order_items__returns_summarized` and `int_orders__refunds_summarized` (views).
- **Marts** (`models/marts/`): the nine tables consumers query, with enforced contracts ([Querying the marts](querying-the-marts.md)).

### Hooks and `make grant-read`

Three hooks in `data-platform/dbt/dbt_project.yml` do work outside the models:

- `on-run-start` runs the `ensure_extensions` macro: it creates the `extensions` schema, installs `pg_trgm` there and puts the schema on the database's search path. It first checks that the database name, `SHERPA_AGENT_USER` and `SHERPA_UI_USER` match `^[a-z_][a-z0-9_]*$` (the `validate_identifier` macro), because they are spliced into SQL unquoted; any other value stops the run with, for example, `SHERPA_AGENT_USER must match ^[a-z_][a-z0-9_]*$, got '...'`.
- A `post-hook: analyze {{ this }}` on every mart, and on the `int_events__sessionized` table, refreshes its table statistics after it is built, so Postgres plans queries well.
- `on-run-end` runs two macros, in this order. `record_run_results` appends one row per executed node to `ops.dbt_run_results` (it creates the `ops` schema and the table if missing), on every target; `is_full_run` is false when the invocation used `--select`, `--exclude` or `--selector`. `grant_read_access` then gives the `sherpa_agent` and `sherpa_ui` roles `USAGE` on the `marts` and `extensions` schemas and `SELECT` on the mart tables, and the UI role `USAGE` on `ops` and `SELECT` on `ops.dbt_run_results`; it does this on `prod` and `eval` only (on `dev`: `grant_read_access: skipped on dev`).

`make grant-read` runs `grant_read_access` on prod without a build, which re-applies the grants if they were lost (`run-operation` runs no hooks, so it records no run results):

```
grant_read_access: granted usage on marts, extensions and select on marts tables to "sherpa_agent" on prod
```

`dbt build` and `dbt source freshness` also run the hooks, so on a fresh database the grants step prints `skipped ... schema marts not built yet` until the marts exist.

## Building

```bash
make deps        # install the dbt packages (dbt_utils, dbt_expectations) into data-platform/dbt/dbt_packages/
make build       # source freshness, then dbt build on dev
make build-prod  # the same on prod
```

`make deps` downloads the versions pinned in `data-platform/dbt/package-lock.yml`: `dbt_utils`, `dbt_expectations` and `dbt_date`, which `dbt_expectations` needs:

```
cd data-platform/dbt && DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt deps
22:23:47  Running with dbt=1.12.6
22:23:50  Installing dbt-labs/dbt_utils
22:23:52  Installed from version 1.4.1
22:23:52  Up to date!
22:23:52  Installing metaplane/dbt_expectations
22:23:54  Installed from version 0.10.10
22:23:54  Up to date!
22:23:54  Installing godatadriven/dbt_date
22:23:57  Installed from version 0.21.0
22:23:57  Up to date!
```

`dbt build` runs seeds, models and tests together in DAG order, and skips whatever depends on a failure. `make build` took about 63 seconds on a year of data, source freshness included (the build itself 52 s):

```
cd data-platform/dbt && DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt build
23:10:41  [WARNING]: Configuration paths exist in your dbt_project.yml file which do not apply to any resources.
There are 1 unused configuration paths:
- snapshots.sherpa
23:10:41  Found 38 models, 281 data tests, 3 seeds, 2 operations, 21 sources, 923 macros
23:10:41  Concurrency: 4 threads (target='dev')
...
23:11:33  Finished running 2 project hooks, 3 seeds, 10 table models, 281 data tests, 24 view models in 0 hours 0 minutes and 52.43 seconds (52.43s).
23:11:34  Done. PASS=320 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=320
```

The 38 models are 24 views, 10 tables and 4 ephemeral models (which build nothing); the 2 operations are the `on-run-start` and `on-run-end` hooks.

The warning is expected: `dbt_project.yml` configures a schema for snapshots and the project has no snapshots. `make build-prod` prints the same with `target='prod'` and schemas without the prefix (`marts.dim_date`).

`make seed` loads only the seeds, which is handy after editing a CSV:

```
12:52:02  1 of 3 OK loaded seed file dev_seeds.channel_mapping ........................... [INSERT 5 in 0.17s]
12:52:02  3 of 3 OK loaded seed file dev_seeds.country_code_variants ..................... [INSERT 18 in 0.17s]
12:52:02  2 of 3 OK loaded seed file dev_seeds.countries ................................. [INSERT 8 in 0.18s]
```

### Source freshness

`dbt build` does not check whether the raw data is recent, so `make build` runs `dbt source freshness` first. Four source tables declare a `freshness` block in their `_<source>__sources.yml`: dbt compares the newest `loaded_at_field` with the current time.

| Source table | Field | Warn after | Error after |
|---|---|---|---|
| `shop.orders` | `updated_at` | 36 h | 72 h |
| `events.events` | `received_at` | 36 h | 72 h |
| `crm.leads` | `updated_at` | 36 h | 72 h |
| `marketing.ad_spend_daily` | `spend_date` | 48 h | never |

On data that reaches yesterday every check passes:

```
12:48:11  3 of 4 PASS freshness of marketing.ad_spend_daily .............................. [PASS in 0.07s]
12:48:11  1 of 4 PASS freshness of crm.leads ............................................. [PASS in 0.11s]
12:48:11  2 of 4 PASS freshness of events.events ......................................... [PASS in 0.11s]
12:48:11  4 of 4 PASS freshness of shop.orders ........................................... [PASS in 0.12s]
```

Warnings let the build continue; an error stops `make build` before `dbt build`. On a dataset whose last day was three weeks ago:

```
13:03:23  1 of 4 ERROR STALE freshness of crm.leads ...................................... [ERROR STALE in 0.04s]
13:03:23  2 of 4 ERROR STALE freshness of events.events .................................. [ERROR STALE in 0.06s]
13:03:23  3 of 4 WARN freshness of marketing.ad_spend_daily .............................. [WARN in 0.06s]
13:03:23  4 of 4 ERROR STALE freshness of shop.orders .................................... [ERROR STALE in 0.07s]
...
make: *** [Makefile:...: build] Error 1
```

The fix is to top the data up to yesterday (`make generate DAYS=<n>`, see the generator guide), not to loosen the thresholds.

## Running dbt directly

The Make targets wrap one command line. To run any other dbt command, use the same one from the `data-platform/dbt/` folder:

```bash
cd data-platform/dbt
DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt <command>
```

`DBT_PROFILES_DIR=.` points dbt at `data-platform/dbt/profiles.yml` and `--env-file ../../.env` supplies the `SHERPA_*` variables it reads. The examples below run from `data-platform/dbt/` with that prefix, shortened to `dbt`. Add `--target prod --target-path target-prod` to run on prod. `dbt debug` checks the setup and the connection; it ends with:

```
12:52:09    Connection test: [OK connection ok]

12:52:09  All checks passed!
```

### Selecting what to run

`--select` (`-s`) takes model names, folders, sources, tags and graph operators. `dbt ls` lists what a selection matches without running anything:

```bash
dbt ls --select staging.shop --resource-type model   # every model in models/staging/shop
dbt ls --select +stg_shop__customers                 # the model and everything upstream of it
dbt ls --select source:events+                       # everything downstream of the events source
```

```
$ dbt --quiet ls --select +stg_shop__customers
sherpa.staging.shop.stg_shop__customers
sherpa.country_code_variants
source:sherpa.shop.customers
sherpa.assert_no_activity_after_customer_deletion
sherpa.not_null_country_code_variants_code
...
sherpa.staging.shop.unique_stg_shop__customers_customer_id
```

Upstream of a staging model are its source and the seeds it joins; the tests attached to them come along, including the singular test that reads the model. `--quiet` drops the log lines.

To rebuild one model and run its tests:

```bash
dbt build --select stg_shop__payments
```

```
12:53:17  1 of 9 START sql view model dev_staging.stg_shop__payments ..................... [RUN]
12:53:17  1 of 9 OK created sql view model dev_staging.stg_shop__payments ................ [CREATE VIEW in 0.15s]
12:53:17  2 of 9 START test accepted_values_stg_shop__payments_method__card__paypal__gift_card  [RUN]
...
12:53:18  9 of 9 PASS unique_stg_shop__payments_payment_id ............................... [PASS in 0.10s]
12:53:18  8 of 9 PASS relationships_stg_shop__refunds_payment_id__payment_id__ref_stg_shop__payments_  [PASS in 0.13s]
12:53:18  7 of 9 PASS relationships_stg_shop__payments_order_id__order_id__ref_stg_shop__orders_  [PASS in 0.13s]
12:53:18
12:53:18  Done. PASS=9 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=9
```

`dbt run` only builds models and `dbt test` only runs tests; both take the same `--select`. A test on another model that references this one (here the `relationships` test of `stg_shop__refunds`) runs too.

One source's freshness: `dbt source freshness --select source:events`.

### Previewing with `dbt show`

`dbt show` runs a model's query and prints the first rows without creating anything:

```bash
dbt show --select stg_shop__customers --limit 3
```

```
Previewing node 'stg_shop__customers':
| customer_id | first_name | last_name | marketing_opt_in |            signup_at |           updated_at | ... |
| ----------- | ---------- | --------- | ---------------- | -------------------- | -------------------- | --- |
|           1 | Heinz      | Stolze    |            False | 2025-10-23 19:11:... | 2025-10-23 19:11:... | ... |
|           2 | Diana      | Cook      |             True | 2025-10-23 19:06:... | 2025-10-23 19:06:... | ... |
|           3 | Maurice    | Berthelot |             True | 2025-10-23 12:45:... | 2025-10-23 12:45:... | ... |
```

`--inline` runs any query and resolves `ref()` and `source()`. The 18 raw country values (see the generator guide) come out as 8 clean codes:

```bash
dbt show --inline "select country_code, count(*) as customers from {{ ref('stg_shop__customers') }} group by 1 order by 2 desc" --limit 10
```

```
Previewing inline node:
| country_code | customers |
| ------------ | --------- |
| DE           |      1500 |
| FR           |       988 |
| ES           |       730 |
| IT           |       613 |
| NL           |       402 |
| GB           |       382 |
| AT           |       203 |
| BE           |       133 |
```

### Seeing the SQL with `dbt compile`

`dbt compile` renders the Jinja into the SQL dbt would run, without running it:

```bash
dbt compile --select stg_shop__payments
```

```
Compiled node 'stg_shop__payments' is:
with

source as (

    select * from "sherpa"."raw_shop"."payments"

),
...
```

Every compiled model and test is also written to `data-platform/dbt/target/compiled/sherpa/...`, and the statements dbt actually ran (with the `CREATE VIEW` around them) to `data-platform/dbt/target/run/sherpa/...`.

## Documentation and the lineage graph

```bash
make docs
```

`make docs` runs `dbt docs generate` (which writes `manifest.json`, `catalog.json` and `index.html` to `data-platform/dbt/target/`) and then `dbt docs serve`, a local web server that opens a browser tab:

```
08:09:31  Running with dbt=1.12.6
Serving docs at 8080
To access from your browser, navigate to: http://localhost:8080

Press Ctrl+C to exit.
```

Stop it with Ctrl+C. `make docs-prod` does the same for prod from `data-platform/dbt/target-prod/`.

The landing page is the `__overview__` doc block in `data-platform/dbt/models/overview.md`. On the left, the **Project** tab groups the nodes by folder and the **Database** tab by schema. A model page shows its description, its columns with their tests, the SQL (source and compiled) and what it depends on.

The **lineage graph** button (bottom right) draws the DAG: sources on the left, flowing into the staging views, the intermediate models and the marts, with the seeds feeding the models that join them (`country_code_variants` into `stg_shop__customers` and `stg_shop__addresses`). The `channel_mapping` seed feeds `int_events__sessionized`, and `dim_date` stands alone because it reads nothing. The `--select` box at the bottom takes the same syntax as the CLI: `+stg_shop__customers` shows the model with its source and seed. Click a node to open its page.

`make docs-check` regenerates the docs quietly and fails if a model or column under `data-platform/dbt/models` has no description; it prints nothing when everything is documented.

## When a test fails

To see what a failure looks like, break the R4 cleanup in `data-platform/dbt/models/staging/shop/stg_shop__customers.sql` by keeping the raw country instead of the seed's code:

```sql
        source.country_code as country_code,   -- was: coalesce(country_variants.code, source.country_code)
```

```bash
dbt build --select stg_shop__customers
```

```
12:53:48  8 of 10 FAIL 162 relationships_stg_shop__customers_country_code__code__ref_countries_  [FAIL 162 in 0.10s]
...
12:53:50  Completed with 1 error, 0 partial successes, and 0 warnings:
12:53:50
12:53:50  [ERROR]: in test relationships_stg_shop__customers_country_code__code__ref_countries_ (models\staging\shop\_shop__models.yml)
12:53:50    Got 162 results, configured to fail if != 0
12:53:50
12:53:50    compiled code at target\compiled\sherpa\models\staging\shop\_shop__models.yml\relationships_stg_shop__custom_6c188ed7d4b2e24f094ea0def18037b4.sql
12:53:50
12:53:50  Done. PASS=9 WARN=0 ERROR=1 SKIP=0 NO-OP=0 REUSED=0 TOTAL=10
```

The test name says what broke: `relationships` from `stg_shop__customers.country_code` to `countries.code`, with 162 rows that have no match. `dbt build` still created the view (a model is built before its tests), but anything downstream of a failed test would be skipped.

To investigate, open the compiled test it points to. It is a plain query you can paste into psql:

```sql
with child as (
    select country_code as from_field
    from "sherpa"."dev_staging"."stg_shop__customers"
    where country_code is not null
),

parent as (
    select code as to_field
    from "sherpa"."dev_seeds"."countries"
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null
```

Or let dbt keep the failing rows in a table with `--store-failures`:

```bash
dbt test --select stg_shop__customers --store-failures
```

```
12:53:59    See test failures:
  --------------------------------------------------------------------------------------------------------------
  select * from "sherpa"."dev_dbt_test__audit"."relationships_stg_shop__custom_6c188ed7d4b2e24f094ea0def18037b4"
  --------------------------------------------------------------------------------------------------------------
```

```sql
SELECT from_field, count(*)
FROM dev_dbt_test__audit.relationships_stg_shop__custom_6c188ed7d4b2e24f094ea0def18037b4
GROUP BY 1 ORDER BY 2 DESC;
```

```
 from_field | count
------------+-------
 de         |    55
 fr         |    36
 es         |    20
 it         |    16
 nl         |    13
 at         |     8
 uk         |     5
 UK         |     4
 be         |     3
 gb         |     2
```

Exactly the noisy codes of R4, so the seed join is missing. Restore the line, rerun `dbt build --select stg_shop__customers` (`PASS=10`), and drop the audit schema (`make reset` also removes it):

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa -c "DROP SCHEMA dev_dbt_test__audit CASCADE"
```

### Reading a contract failure

Each mart's YAML lists every column with its `data_type`. dbt compares that list with the model's real columns before it creates the table, so a mismatch stops the build, and nothing is replaced: the previous table stays in place and the model's tests are skipped. There are three common mistakes.

**A column in the SQL that the YAML does not list.** Adding `variants.list_price * 2 as double_price` to `dim_products.sql` and nothing to its YAML:

```bash
dbt build --select dim_products
```

```
  This model has an enforced contract that failed.
  Please ensure the name, data_type, and number of columns in your contract match the columns in your model's definition.

  | column_name  | definition_type | contract_type | mismatch_reason     |
  | ------------ | --------------- | ------------- | ------------------- |
  | double_price | DECIMAL         |               | missing in contract |

  > in macro assert_columns_equivalent (macros\relations\column\columns_spec_ddl.sql)
  ...
  > called by model dim_products (models\marts\products\dim_products.sql)
...
Done. PASS=2 WARN=0 ERROR=1 SKIP=7 NO-OP=0 REUSED=0 TOTAL=10
```

**A YAML column without a `data_type`:**

```
  Contracted models require data_type to be defined for each column. Please ensure that the column name and data_type are defined within the YAML configuration for the ['current_margin'] column(s).
```

**A type that differs from the SQL.** The YAML says `numeric(12,2)` and the SQL produces text:

```
  | column_name    | definition_type | contract_type | mismatch_reason    |
  | -------------- | --------------- | ------------- | ------------------ |
  | current_margin | TEXT            | DECIMAL       | data type mismatch |
```

`definition_type` is what the SQL produces and `contract_type` what the YAML declares. The same table lists a column the YAML declares but the SQL does not produce. [Adding or changing a mart](../advanced/adding-or-changing-a-mart.md) walks through the fix.

### Reading a `column_sums_match` failure

`column_sums_match` is a test we wrote: for each value of a grouping column, the sum of a column in one model must equal the sum of a column in another. It guards totals that two marts must agree on, for example `fct_order_items.gross_amount` against `fct_orders.gross_revenue` per `order_id`. It returns one row per group that differs: the group, the model's sum and the other model's sum. A group that exists on only one side shows 0 for the other. If the lines and the orders disagree for orders 1 and 2, the failing rows look like this (the test's own SQL, run with the order side shifted by 10 EUR for those two orders to show the shape):

```
 order_id | model_sum | compare_sum
----------+-----------+-------------
        2 |     69.98 |       79.98
        1 |    201.75 |      211.75
```

Order 2's lines add up to 69.98 but the order says 79.98, so one of the two models computes it wrongly. Keep the rows with `dbt test --select fct_order_items --store-failures` (as above), or open the compiled test under `data-platform/dbt/target/compiled/sherpa/models/marts/sales/_sales__models.yml/`.

If something goes wrong with dbt itself rather than the data, `dbt debug` checks the connection, and `--no-partial-parse` forces dbt to re-read every file when it seems to ignore an edit.

## The frozen eval database

`make eval-db` builds a second, separate database, `sherpa_eval`, that never changes: the generator runs with seed 42, start date 2025-01-01 and 365 days, then dbt builds the `eval` target into it and writes its artifacts to `data-platform/dbt/target-eval/`. The text-to-SQL agent is scored against this database, so the expected answers to its test questions stay valid as long as the generator and the dbt project do not change.

```bash
make eval-db   # about 5 minutes or more
```

It drops `sherpa_eval` if it exists and recreates it empty, so it is safe to rerun, and it never touches the main `sherpa` database. The name comes from `SHERPA_EVAL_DB_NAME` (default `sherpa_eval`), which make reads from the environment or the command line, not from `.env`. The target refuses a name that does not start with `sherpa_eval`, contains anything but lowercase letters, digits and `_`, or equals the main database, so a typo cannot drop the wrong database:

```bash
make eval-db SHERPA_EVAL_DB_NAME=sherpa
```

```
Makefile:...: *** SHERPA_EVAL_DB_NAME must start with sherpa_eval and differ from the main database 'sherpa', got 'sherpa'.  Stop.
```

The build skips the source freshness check, because the frozen data is old on purpose. For the same reason one test warns on this database, so the eval build ends with `WARN=1`, and the warning is expected: the check that `stg_events__events.received_at` has data from the last 3 days. To run just that test:

```bash
cd data-platform/dbt
DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt test --target eval --target-path target-scratch --select stg_events__events,test_name:expect_row_values_to_have_recent_data
```

```
21:01:41  1 of 1 WARN 1 dbt_expectations_expect_row_values_to_have_recent_data_stg_events__events_received_at__day__3
21:01:42  [WARNING]: Got 1 result, configured to warn if != 0
21:01:42  Done. PASS=2 WARN=1 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=3
```

The command writes its artifacts to the scratch folder `target-scratch`, so the frozen eval artifacts in `target-eval` stay as `make eval-db` left them.

The `on-run-end` hook gives `sherpa_agent` the same read access to the eval marts as to prod. Query it with `-d sherpa_eval` ([Querying the marts](querying-the-marts.md#connecting-as-sherpa_agent)).

## Tests that need the warehouse

Most Python tests need nothing but Postgres (`integration` marker). Tests marked `warehouse` also need the built warehouses, because they read them: run `make build-prod` first, `make build` as well for the one test that proves the agent cannot read `dev_marts`, and `make eval-db` for the tests that run against `sherpa_eval`. `make test` leaves them out, like the `slow` tests; run them with:

```bash
uv run --env-file .env pytest -m warehouse
```

```
collected 325 items / 288 deselected / 37 selected

tests\dbt\test_reader_access.py .....................................     [100%]

===================== 37 passed, 288 deselected in 3.36s ======================
```

They check, on both the main and the eval database, that `sherpa_agent` can read all nine marts, cannot read staging, intermediate, seeds or raw, has no `USAGE` on `public`, cannot create temporary tables or large objects, can use `similarity()` without a schema prefix, and can read the table comments, and that `marts.dim_products` has its five trigram indexes; and, on the main database, that it cannot read `dev_marts`. Before each denied read, an admin connection checks that the table exists, so a missing table cannot pass for a missing privilege.

## Next

- [Using the generator](generator-cli.md): make or refresh the data dbt reads.
- [Extending the source data model](../advanced/extending-the-source-data-model.md): add a column or table end to end, including its staging model and tests.
