# Building the warehouse

dbt turns the raw schemas the generator writes into tested, documented models. Setup is in the [README Quickstart](../../../README.md#quickstart), data in [Using the generator](generator-cli.md), models and tests in the [data platform spec](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) (sections 6 and 7).

## Dev and prod

A **target** is a named connection in `data-platform/dbt/profiles.yml`: `dev` (default), `prod` and `eval`. The `generate_schema_name` macro puts each layer in its own schema: `dev_seeds`, `dev_staging`, `dev_intermediate`, `dev_marts` on `dev`; the same names without the prefix on `prod`. Use `dev` while you work. `prod` is what the agent and the UI read; its artifacts go to `data-platform/dbt/target-prod/` (prod commands pass `--target-path target-prod`).

## The layers

- **Staging** (`models/staging/`): one view per raw table; renames, casts, cleaning.
- **Intermediate** (`models/intermediate/`): joins several marts share, mostly *ephemeral* (pasted in as a CTE, nothing created in Postgres).
- **Marts** (`models/marts/`): the nine tables consumers query, with enforced contracts ([Querying the marts](querying-the-marts.md)).

## Building

```bash
make build       # source freshness, then dbt build on dev (installs dbt packages when package-lock.yml changed)
make build-prod  # the same on prod, then dbt docs generate
```

`dbt build` runs seeds (CSVs in `data-platform/dbt/seeds/`), models and tests in DAG order and skips whatever depends on a failure. It takes about a minute on a year of data. The `snapshots.sherpa` unused-path warning is expected. `make build-prod` also writes `target-prod/catalog.json`, which the MCP server and the UI read.

### Source freshness

Both targets run `dbt source freshness` first. `shop.orders`, `events.events` and `crm.leads` warn after 36 h and error after 72 h; `marketing.ad_spend_daily` warns after 48 h. An error stops the build before `dbt build`:

```
13:03:23  1 of 4 ERROR STALE freshness of crm.leads ...................................... [ERROR STALE in 0.04s]
...
make: *** [Makefile:...: build] Error 1
```

### Hooks and grants

Three hooks in `dbt_project.yml` work outside the models:

- `on-run-start` runs `ensure_extensions`: the `extensions` schema with `pg_trgm` (fuzzy search).
- `post-hook: analyze {{ this }}` on every mart refreshes table statistics.
- `on-run-end` runs `record_run_results` (every target) and `grant_read_access` (`prod` and `eval` only), which gives `sherpa_agent` and `sherpa_ui` read access to the marts.

Re-apply the grants without a build:

```bash
cd data-platform/dbt
DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt run-operation grant_read_access --target prod --target-path target-prod
```

### The audit table `ops.dbt_run_results`

`record_run_results` appends one row per node of every invocation that runs the hooks (`invocation_id`, `target_name`, `command`, `is_full_run`, `unique_id`, `status`, `failures`, `message`, ...); `run_results.json` is overwritten each command, the table is the history. Filter on `command = 'build'` for the last full build:

```sql
select run_started_at, count(*) as nodes,
       count(*) filter (where status in ('error', 'fail')) as failed
from ops.dbt_run_results
where target_name = 'prod' and command = 'build' and is_full_run
group by invocation_id, run_started_at
order by run_started_at desc limit 1;
```

## Running dbt directly

Run any other dbt command from `data-platform/dbt/`:

```bash
cd data-platform/dbt
DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt <command>
```

Below, that prefix is shortened to `dbt`. Add `--target prod --target-path target-prod` for prod; `dbt debug` checks the connection.

**Selecting.** `--select` takes names, folders, sources and graph operators (`+model` upstream, `source:events+` downstream). `dbt ls` previews a selection, `dbt build` runs it with its tests:

```bash
dbt ls --select +stg_shop__customers
dbt build --select stg_shop__payments
```

**Previewing.** `dbt show` runs a query and prints rows without creating anything; `--inline` resolves `ref()` and `source()`:

```bash
dbt show --select stg_shop__customers --limit 3
dbt show --inline "select country_code, count(*) from {{ ref('stg_shop__customers') }} group by 1 order by 2 desc" --limit 10
```

**Compiling.** `dbt compile --select stg_shop__payments` prints the rendered SQL. Compiled models and tests are also written to `target/compiled/sherpa/...`, and the statements dbt ran to `target/run/sherpa/...`.

## Documentation and the lineage graph

`make docs` generates and serves the dev docs at `http://localhost:8080` (Ctrl+C to stop). A model page shows its columns with their tests, SQL and dependencies; the lineage graph button draws the DAG. `make lint` includes the check that every model and column has a description.

## When a test fails

A data test is a query that returns the rows breaking a rule; zero rows passes, and the test name says what broke. With the R4 country cleanup broken in `stg_shop__customers.sql`, `dbt build --select stg_shop__customers` gives:

```
12:53:48  8 of 10 FAIL 162 relationships_stg_shop__customers_country_code__code__ref_countries_  [FAIL 162 in 0.10s]
...
12:53:50    Got 162 results, configured to fail if != 0
12:53:50    compiled code at target\compiled\sherpa\models\staging\shop\_shop__models.yml\relationships_stg_shop__custom_6c188ed7d4b2e24f094ea0def18037b4.sql
```

That is `relationships` from `stg_shop__customers.country_code` to `countries.code`, with 162 unmatched rows. The compiled file is a plain query for psql. With `--store-failures`, dbt keeps the failing rows in a table in `dev_test_failures` (dropped by `make reset`) and prints the `select` to read it. Singular tests and `column_sums_match` always store theirs there.

### Reading a contract failure

dbt compares each mart's YAML columns and `data_type`s with the model's real columns before creating the table; a mismatch stops the build and skips the model's tests:

```
  | column_name    | definition_type | contract_type | mismatch_reason    |
  | -------------- | --------------- | ------------- | ------------------ |
  | double_price   | DECIMAL         |               | missing in contract |
  | current_margin | TEXT            | DECIMAL       | data type mismatch |
```

`definition_type` is what the SQL produces, `contract_type` what the YAML declares; [Adding or changing a mart](../advanced/adding-or-changing-a-mart.md) walks through the fix.

### Reading a `column_sums_match` failure

`column_sums_match` (`data-platform/dbt/tests/generic/column_sums_match.sql`) checks that per group a column's sum in one model equals the sum in another, for example `fct_order_items.gross_amount` against `fct_orders.gross_revenue` per `order_id`. It returns one row per group that differs:

```
 order_id | model_sum | compare_sum
----------+-----------+-------------
        2 |     69.98 |       79.98
```

Order 2's lines add up to 69.98 but the order says 79.98, so one model is wrong. Keep the rows with `--store-failures`.

### Common errors

| Symptom | Fix |
|---|---|
| `make build` stops with `ERROR STALE` | Source data is older than 72 h: `make advance DAYS=<n>` |
| `role "sherpa_ui" does not exist` at the grants | `make up` re-applies the roles, then build again |
| `enforced contract that failed` | Make the mart's YAML columns and `data_type`s match the SQL |
| `make eval-db` refuses `SHERPA_EVAL_DB_NAME` | The name must start with `sherpa_eval` and differ from the main database |

## The frozen eval database

`make eval-db` drops and rebuilds a separate database, `sherpa_eval` (seed 42, 2025-01-01, 365 days), and builds the `eval` target into it (artifacts in `data-platform/dbt/target-eval/`). The agent is scored against it, so expected answers stay valid while the generator and dbt project do not change. It never touches the main database and skips source freshness; the build ends with the expected `WARN=1` from `expect_row_values_to_have_recent_data` on `stg_events__events.received_at`. Query it with `-d sherpa_eval` ([Querying the marts](querying-the-marts.md#connecting-as-sherpa_agent)).

## Tests that need the warehouse

Tests marked `warehouse` read the built warehouses: run `make build-prod` first (plus `make build` for the `dev_marts` test, `make eval-db` for the `sherpa_eval` tests). `make test` leaves them out:

```bash
uv run --env-file .env pytest -m warehouse
```
