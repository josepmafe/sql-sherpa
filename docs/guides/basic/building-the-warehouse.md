# Building the warehouse

dbt turns the raw schemas the generator writes into tested, documented models. Setup is in the [README Quickstart](../../../README.md#quickstart), data in [Using the generator](generator-cli.md), models and tests in the [data platform spec](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) (sections 6 and 7).

## Dev and prod

A **target** is a named connection in `data-platform/dbt/profiles.yml`: `dev` (default), `prod` and `eval`. The `generate_schema_name` macro puts each layer in its own schema: `dev_seeds`, `dev_staging`, `dev_intermediate`, `dev_marts` on `dev`; the same names without the prefix on `prod`. Use `dev` while you work. `prod` is what the agent and the UI read; its artifacts go to `data-platform/dbt/target-prod/` (prod commands pass `--target-path target-prod`).

## The layers

- **Staging** (`models/staging/`): one view per raw table; renames, casts, cleaning.
- **Snapshots** (`snapshots/`): `snap_product_variants` keeps every price version of a product variant.
- **Intermediate** (`models/intermediate/`): joins several marts share, mostly *ephemeral* (pasted in as a CTE, nothing created in Postgres).
- **Marts** (`models/marts/`): the tables consumers query, with enforced contracts ([Querying the marts](querying-the-marts.md)).

## Building

```bash
make build       # source freshness, then dbt build on dev (installs dbt packages when package-lock.yml changed)
make build-prod  # the same on prod, then dbt docs generate
```

`dbt build` runs seeds (CSVs in `data-platform/dbt/seeds/`), the snapshot, models, unit tests and data tests in DAG order and skips whatever depends on a failure. A unit test runs on fixed rows before its model builds; a failing one prints the differing cells as `actual differs from expected`. The first build takes about two minutes on a year of data, later ones under a minute. The `snapshots.sherpa` unused-path warning is expected. `make build-prod` also writes `target-prod/catalog.json`, which the MCP server and the UI read.

### Keeping it current

Three models are incremental, so a build processes only what is new ([why each strategy](../advanced/adding-or-changing-a-mart.md#7-change-an-incremental-model)). `stg_events__events` is a microbatch model: each run reprocesses today and the 3 days before, counted from today (not from the data), one batch per day, with `begin` set to the simulation's first day by `make build`:

```
Batch 1 of 4 OK created batch 2026-10-23 of dev_staging.stg_events__events ... [MERGE 1448 in 0.57s]
Batch 4 of 4 OK created batch 2026-10-26 of dev_staging.stg_events__events ... [MERGE 0 in 0.31s]
```

The other two (`int_identity__anonymous_to_customer`, `int_events__sessionized`) read only events newer than their own newest `received_at`. A second `make build` right after the first therefore changes almost nothing.

- **Price history.** `dbt snapshot` (run by `make advance` and `make backfill`, and inside every `dbt build`) closes a variant's old row in `snapshots.snap_product_variants` when its price changes. `dbt_valid_to` is empty on the current version:

  ```sql
  select id, cost_price, list_price, dbt_valid_from::date, dbt_valid_to::date
  from snapshots.snap_product_variants where id = 810 order by dbt_valid_from;
  ```

  ```
   id  | cost_price | list_price | dbt_valid_from | dbt_valid_to
  -----+------------+------------+----------------+--------------
   810 |       5.51 |      10.99 | 2025-10-26     | 2025-12-27
   810 |       5.51 |       9.82 | 2025-12-27     | 2026-02-28
  ```

- **`--full-refresh`.** `dbt build --full-refresh` drops and rebuilds the incremental models from scratch (for `stg_events__events`, a batch per simulated day, about two minutes on a year), and all other models as usual. Snapshots ignore it: the old versions exist nowhere else.
- **`dbt retry`.** After a failed build, `dbt retry` (same target) reruns only the failed and skipped nodes. A failing unit test stops its model from building, so a typo in `fct_sessions.sql` fails `fct_sessions_aggregates_a_session` first; after the fix, retry runs that unit test, `fct_sessions`, its tests and the skipped nodes, and ends `Completed successfully`.
- **Incremental equals full refresh.** After `dbt build --full-refresh --target prod --target-path target-prod`, compare it with the dev table (zero rows means identical):

  ```bash
  dbt show --select fct_sessions_incremental_vs_full_refresh --vars '{compare_schema: marts}'
  ```

### Source freshness

Both targets run `dbt source freshness` first. `shop.orders`, `events.events` and `crm.leads` warn after 36 h and error after 72 h; `marketing.ad_spend_daily` warns after 48 h. An error (`ERROR STALE freshness of crm.leads`) stops the build before `dbt build`; `make advance DAYS=<n>` brings the data up to date.

### Hooks and grants

Three hooks in `dbt_project.yml` work outside the models:

- `on-run-start` runs `ensure_extensions`: the `extensions` schema with `pg_trgm` (fuzzy search).
- `post-hook: analyze {{ this }}` on every mart refreshes table statistics.
- `on-run-end` runs `record_run_results` (every target) and `grant_read_access` (`prod` and `eval` only), which gives `sherpa_agent` and `sherpa_ui` read access to the marts.

Re-apply the grants without a build with `dbt run-operation grant_read_access --target prod --target-path target-prod` (the prefix is explained under "Running dbt directly").

The table `ops.dbt_run_results` keeps one row per node of every invocation (`target_name`, `command`, `is_full_run`, `unique_id`, `status`, ...), the history that `run_results.json` overwrites.

## Running dbt directly

Run any other dbt command from `data-platform/dbt/`:

```bash
cd data-platform/dbt
DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt <command>
```

Below, that prefix is shortened to `dbt`. Add `--target prod --target-path target-prod` for prod; `dbt debug` checks the connection.

**Selecting.** `--select` takes names, folders, sources and graph operators (`+model` upstream, `source:events+` downstream). `dbt ls` previews a selection, `dbt build` runs it with its tests:

```bash
dbt build --select stg_shop__payments
```

**Previewing.** `dbt show` runs a query and prints rows without creating anything; `--inline` resolves `ref()` and `source()`:

```bash
dbt show --select stg_shop__customers --limit 3
```

## Documentation and the lineage graph

`make docs` generates and serves the dev docs at `http://localhost:8080` (Ctrl+C to stop). A model page shows its columns with their tests, SQL and dependencies; the lineage graph button draws the DAG.

## When a test fails

A data test is a query that returns the rows breaking a rule; zero rows passes, and the test name says what broke. Singular tests (one rule, one query) are in `data-platform/dbt/tests/singular/`. With the R4 country cleanup broken in `stg_shop__customers.sql`, `dbt build --select stg_shop__customers` gives:

```
12:53:48  8 of 10 FAIL 162 relationships_stg_shop__customers_country_code__code__ref_countries_  [FAIL 162 in 0.10s]
12:53:50    Got 162 results, configured to fail if != 0
```

That is `relationships` from `stg_shop__customers.country_code` to `countries.code`, with 162 unmatched rows. The compiled file named in the failure output is a plain query you can run in psql. With `--store-failures`, dbt keeps the failing rows in a table in `dev_test_failures` (dropped by `make reset`) and prints the `select` to read it. Singular tests and `column_sums_match` always store theirs there.

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

`column_sums_match` (`data-platform/dbt/tests/generic/column_sums_match.sql`) checks that per group a column's sum in one model equals the sum in another (for example `fct_order_items.gross_amount` against `fct_orders.gross_revenue` per `order_id`) and returns one row per group that differs: `order_id | model_sum | compare_sum` with `2 | 69.98 | 79.98` means order 2's lines add up to 69.98 but the order says 79.98, so one model is wrong.

### Common errors

| Symptom | Fix |
|---|---|
| `make build` stops with `ERROR STALE` | Source data is older than 72 h: `make advance DAYS=<n>` |
| `assert_every_source_event_is_staged` fails after `make advance` of more than 3 days | The microbatch skipped the oldest days; the failing rows list them. Stage them, then build again: `dbt build --select stg_events__events --event-time-start 2026-10-19 --event-time-end 2026-10-23` (`--event-time-end` is exclusive: the day after the last missing day; here 19 to 22 October are missing) |
| `--- Logging error ---` and a `UnicodeEncodeError` instead of a unit-test diff (Windows) | `export PYTHONUTF8=1` |
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
