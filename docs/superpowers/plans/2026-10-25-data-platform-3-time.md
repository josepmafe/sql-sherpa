# data-platform-3 — Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the warehouse handle time. A snapshot keeps price history, so cost at sale is the cost when the order was placed. Events load incrementally (microbatch), and so do sessions (delete+insert) and the device-to-customer map (merge). `make advance` moves the simulation forward, and the new mart `fct_sessions` answers traffic and conversion questions.

**Architecture:**
- **Snapshot:** `snap_product_variants` is a YAML snapshot of the raw variants. `make backfill` simulates in 7-day chunks with a snapshot after each (a Python script), and `make advance` simulates N days then snapshots.
- **Incremental models:** three models are incremental, each with its own strategy and a unit test that pins its incremental logic:
  - `stg_events__events`: `microbatch`
  - `int_events__sessionized`: `delete+insert`
  - `int_identity__anonymous_to_customer`: `merge`
- **New mart:** `fct_sessions` (domain `marketing_web`) is a table built from the sessionized events and the device map. The MCP server's scope texts learn about it.
- **Tests:** singular and custom tests keep their failures in `test_failures`. An analysis proves that an incremental build equals a full refresh.

**Tech Stack:** dbt Core 1.12.6 + dbt-postgres 1.11.0 (YAML snapshots, `hard_deletes`, microbatch, unit tests, `dbt retry`), Postgres 16, Python 3.12 (`dbt.cli.main.dbtRunner` in the backfill script), pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`. Read before starting:
- §5.1 (generator CLI), §6.2–§6.5 (layers, `fct_sessions`), §6.6 (metrics), §7 (tests)
- §8 data-platform-3, §10 (Make targets), §11 (interface)

**`CLAUDE.md` is binding.** This plan's commit updates the spec where dbt-postgres or the data forced a decision; the spec text is the reference:
- **Microbatch:** needs `unique_key: event_id` on Postgres (each batch is a MERGE). `begin` is the simulation's first day, from `SHERPA_SIMULATION_START_DATE`. A catch-up beyond the 3-day lookback is caught by a guard test and fixed with a targeted backfill.
- **Device map:** `int_identity__anonymous_to_customer` is built here, because `fct_sessions.customer_id` is stitched through it.
- **Analyses:** they start here, with the incremental-vs-full-refresh diff.
- **Backfill:** `make backfill` only starts a new simulation, and `make eval-db` also takes the chunked snapshots.
- **`fct_sessions`:** the rules for `is_first_session`, `campaign_id` and test-account stitching.

**Base:** `main` with ui-1 (#9), the panel-review rule (#10) and the Make cleanup (#11). The plan relies on:
- ui-1's `grant_read_access`, `data-platform/tests/dbt/test_reader_access.py` and the `ops` schema
- the fifteen Make targets: `make lint` includes the docs-coverage check (private `_docs-check`), `make build` installs the dbt packages through the `$(DBT_PACKAGES)` file target, and helpers that other targets call are private (`_name`)
- the guide rule and the panel-review rule in `CLAUDE.md`

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up (`haiku` → `sonnet` → `opus`). The final whole-branch review is Task 10's panel; don't run a separate one.

## Global Constraints

- **Incremental commits (CLAUDE.md):** each task adds only the files, config, Make targets and dependencies it uses. Every commit leaves `make build` (dev), `make lint` (which includes the docs-coverage check) and `make test` green.
- **dbt conventions:** naming and YAML files as data-platform-2. Every model, column and snapshot has a description.
- **Singular tests** live in `data-platform/dbt/tests/singular/` from Task 1.
- **Unit tests:**
  - they sit under `unit_tests:` in the model's directory YAML
  - each is named `<model>_<behaviour>`
  - `expect` lists only the columns under test
  - every input the model reads is given (`rows: []` when it doesn't matter)
  - ephemeral inputs and `this` use `format: sql` with explicit casts, so the test runs on a fresh database before the model exists
- **Incremental models:**
  - set `on_schema_change='fail'`
  - read their watermark from `{{ this }}` with a `coalesce` to `'-infinity'`, so an empty table reads everything
  - follow the UTC rule: a date is `(<ts> at time zone 'UTC')::date`, and a UTC midnight is `<date>::timestamp at time zone 'UTC'`
- **Unchanged from data-platform-2:**
  - mart contract types, `meta`, `persist_docs`, `analyze`
  - test accounts are excluded everywhere
  - `as_of_date`, never `now()`
- **Severity:** integrity tests are `error`, distribution and freshness tests `warn`. `store_failures` is on for singular and custom generic tests, into schema `test_failures` (`dev_test_failures` on dev).
- **The shared warehouse:** other worktrees, the MCP server and the UI read database `sherpa`.
  - On `sherpa`, run only dev builds (`make build`).
  - Prod builds, `advance`, `backfill` and full refreshes of prod run on a scratch database: `SHERPA_DB_NAME=sherpa_dp3_check`. Existing environment variables win over `.env` for the generator, for `uv run --env-file` and for dbt.
  - Create the scratch database like `make eval-db` does (`DROP DATABASE IF EXISTS … WITH (FORCE)` + `CREATE DATABASE` as the admin), and drop it when the task is done.
- **First build after a model turns incremental:** run the build once with `--full-refresh` on the selected model and its children (`dbt build --full-refresh --select <model>+`). `stg_events__events` changes from a view and `int_events__sessionized` gains columns; `on_schema_change='fail'` makes a missed refresh loud.

## Review Focus

1. **Advancing more than the 3-day lookback in one go** (`make advance DAYS=7`, then a build). Microbatch picks its batches by the wall clock, so the oldest days are skipped. The build must fail before sessionization moves its watermark, and a targeted `--event-time-start/--event-time-end` backfill must repair it with nothing lost. Tests: `assert_every_source_event_is_staged` (Task 4) and Task 8 Step 2.
2. **An incremental run with no new events** (two builds in a row): sessionization deletes and inserts nothing. Test: `int_events__sessionized_emits_nothing_without_new_events` (Task 6).
3. **An empty `{{ this }}`** (the table was built before any event existed): the next run reads every event instead of none. Tests: `int_events__sessionized_reads_all_when_table_empty` (Task 6) and `int_identity__anon_to_customer_reads_all_when_table_empty` (Task 5).
4. **A test account's sessions from before it logged in:** their events carry no customer, but stitching maps the device to the test account. They must not reach `fct_sessions`. Test: `fct_sessions_drops_test_account_sessions` (Task 7).
5. **`make backfill` on a simulation that has already started, or a chunk that fails:**
   - Backfill refuses before generating anything and names `make advance`.
   - A failing chunk stops the backfill without a snapshot.

   Tests: `TestChunkedBackfill::test_refuses_a_simulation_that_has_already_started`, `test_stops_at_a_failing_chunk_without_snapshotting_it` (Task 3).

---

## File Structure

```
data-platform/dbt/
  dbt_project.yml                     # data_tests config, analysis-paths
  snapshots/_snapshots.yml            # snap_product_variants
  analyses/fct_sessions_incremental_vs_full_refresh.sql
  tests/singular/assert_*.sql         # moved from tests/; new ones here
  tests/generic/column_sums_match.sql # + store_failures
  models/staging/events/              # stg_events__events: microbatch; source event_time
  models/intermediate/identity/int_identity__anonymous_to_customer.sql   # merge
  models/intermediate/events/int_events__sessionized.sql                 # delete+insert
  models/intermediate/orders/int_order_items__costed.sql                 # view, from the snapshot
  models/marts/marketing_web/{_marketing_web__models.yml, fct_sessions.sql}
  models/marts/_metrics.md            # + metric_conversion_rate; cost text
data-platform/generator/src/sherpa_generator/cli.py   # + start-date command
data-platform/scripts/backfill.py                     # chunked backfill with snapshots
data-platform/tests/scripts/test_backfill.py
agent/mcp-server/src/sherpa_mcp/{tools.py, examples.yaml}
Makefile
```

---

### Task 1: Failures in `test_failures`, singular tests folder, and the first unit tests

**Model:** implementer `sonnet` · reviewer `sonnet`

**Adds:**
- `git mv data-platform/dbt/tests/assert_*.sql data-platform/dbt/tests/singular/`
- `dbt_project.yml`:
  ```yaml
  data_tests:
    sherpa:
      +schema: test_failures
      singular:
        +store_failures: true
  ```
  A singular test's `fqn` includes its folder (`['sherpa', 'singular', '<name>']`), so the folder key targets only them.
- `{{ config(store_failures=true) }}` inside the `column_sums_match` test block.
- Makefile `DBT_SCHEMAS`: `dev_dbt_test__audit` and `dbt_test__audit` become `dev_test_failures` and `test_failures`.

These unit tests replace `assert_customer_segment_boundaries.sql` and `assert_revenue_excluding_tax.sql`. Delete those two singular tests: data-platform-2 used them as stand-ins because unit tests come with this milestone.

- [ ] **Step 1: Write the unit tests:**
  - **`int_order_items__discount_allocated_remainder_to_largest`** (`_orders__models.yml`):
    - `stg_shop__order_items` (`order_item_id, order_id, quantity, unit_price`):
      - order 1: lines 11, 12, 13, each 1 × 10.00
      - order 2: line 21, 1 × 50.00, and line 22, 1 × 25.00
      - order 3: line 31, 2 × 20.00
      - order 4: lines 41, 42, 43, each 1 × 10.00, and line 44, 1 × 30.00
    - `stg_shop__order_discounts` (`order_id, amount`): (1, 10.00), (2, 5.00), (2, 2.50), (4, 0.10).
    - Expect (`order_item_id, allocated_discount, net_amount`):
      - order 1: 11 → 3.34 / 6.66, 12 → 3.33 / 6.67, 13 → 3.33 / 6.67 (a tie on gross amount goes to the lowest id)
      - order 2: 21 → 5.00 / 45.00, 22 → 2.50 / 22.50
      - order 3: 31 → 0.00 / 40.00
      - order 4: 41, 42 and 43 → 0.02 / 9.98 each, 44 → 0.04 / 29.96 (a negative remainder)
  - **`int_orders__refunds_summarized_untaxes_returns_and_goodwill`:**
    - Refunds (`order_id`, reason, `amount`): order 1 return 120.00 and goodwill 64.80; order 2 cancellation 50.00; order 3 return 1.00; order 4 goodwill 0.01.
    - Expect (`order_id, refunded_amount, refunded_revenue`): 1 → 184.80 / 154.00, 2 → 50.00 / 0.00, 3 → 1.00 / 0.83, 4 → 0.01 / 0.01.
  - **`dim_customers_segments_at_the_90_and_180_day_boundaries`** (`_customers__models.yml`):
    - Customers 1–7 are non-test with country `DE`. `countries` has the `DE` row.
    - `int_identity__lead_to_customer` is ephemeral, so give it as `format: sql` with no rows. `stg_marketing__campaigns` and `int_events__sessionized` are `rows: []`.
    - Non-cancelled `fct_orders` per customer:
      - 1: one order on 2025-04-01
      - 2: one on 2025-03-31
      - 3: two, on 2025-02-01 and 2025-04-01
      - 4: one on 2025-01-01
      - 5: one on 2024-12-31
      - 6: none
      - 7: one on 2025-06-30, which sets `as_of_date`
    - Expect (`customer_id, order_count, days_since_last_order, customer_segment`):
      - 1 → 1 / 90 / new
      - 2 → 1 / 91 / at_risk
      - 3 → 2 / 90 / active
      - 4 → 1 / 180 / at_risk
      - 5 → 1 / 181 / churned
      - 6 → 0 / null / prospect
      - 7 → 1 / 0 / new
- [ ] **Step 2:** `make build`. Expected: the three unit tests pass. The singular tests run from `tests/singular/` and write their tables into `dev_test_failures`; check with `\dt dev_test_failures.*` as admin. The `column_sums_match` instances store theirs too. No other generic test does.
- [ ] **Step 3:** Break one assertion on purpose (expect 3.33 for line 11) and run `make build`. Expected: the unit test fails with dbt's actual-vs-expected diff. Revert.
- [ ] **Step 4:** Add `("test_failures", "assert_buyer_revenue_pareto")` to `NOT_EXPOSED` in `test_reader_access.py`. Neither reader role may read stored failures. That warehouse test runs in Task 8 on the scratch database. Then `make lint && make test`, and fix the paths in any doc that names `dbt/tests/assert_…` (`git grep -n "tests/assert_"`). Commit `test: store test failures in test_failures and add the first dbt unit tests`.

---

### Task 2: The product variants snapshot and cost at sale

**Model:** implementer `sonnet` · reviewer `sonnet`

**Adds:** `data-platform/dbt/snapshots/_snapshots.yml`, and `snapshots` in the Makefile's `DBT_SCHEMAS`.

**Interfaces — Produces:**
- **`snap_product_variants`:**
  ```yaml
  snapshots:
    - name: snap_product_variants
      relation: source('shop', 'product_variants')
      config: {unique_key: id, strategy: timestamp, updated_at: updated_at, hard_deletes: invalidate}
  ```
  It has a description and describes `id`, `cost_price`, `list_price`, `dbt_valid_from` and `dbt_valid_to`. The schema is always `snapshots` (the existing `generate_schema_name`).
- **`int_order_items__costed`** becomes a **view** (`{{ config(materialized='view') }}`) with columns `order_item_id, unit_cost_at_sale`.
  - **Inputs:** `stg_shop__order_items`, `stg_shop__orders` (for `placed_at`) and the snapshot.
  - **The version:** the one with `dbt_valid_from <= placed_at` and (`dbt_valid_to` null or `placed_at < dbt_valid_to`).
  - **Fallback:** without one, the variant's earliest version (the spec's documented limitation for orders before the first captured version).
  - **Tests:** unique + not_null `order_item_id`; not_null `unit_cost_at_sale`; `dbt_utils.equal_rowcount` against `stg_shop__order_items`, because `fct_order_items` inner-joins this model and a line without a cost would vanish.

- [ ] **Step 1: Unit test `int_order_items__costed_uses_cost_valid_at_placement`.** Snapshot rows (`id, cost_price, dbt_valid_from, dbt_valid_to`), all UTC:
  - (1, 10.00, 2025-03-01 00:00, 2025-03-10 12:00)
  - (1, 12.00, 2025-03-10 12:00, null)
  - (2, 7.00, 2025-03-05 00:00, null)

  Lines (`order_item_id`, variant, `placed_at`) and expected `unit_cost_at_sale`:
  - 1, variant 1, 2025-03-05 → 10.00
  - 2, variant 1, 2025-03-10 12:00:00 → 12.00 (the start of a version is inclusive)
  - 3, variant 1, 2025-03-10 11:59:59 → 10.00 (its end is exclusive)
  - 4, variant 1, 2025-02-20 → 10.00 (before the first version)
  - 5, variant 2, 2025-03-01 → 7.00
- [ ] **Step 2:** `make build`. Expected: the snapshot is created in `snapshots`, the unit test passes, and the YAML tests pass. On the shared warehouse the first snapshot holds only current prices, so `select sum(cogs) from dev_marts.fct_order_items` is unchanged from before the change. Record it before you start.
- [ ] **Step 3: Docs that described the current cost:**
  - `_metrics.md` (`metric_cogs`, `metric_gross_margin`): cost at sale comes from the snapshot. State the two limitations: orders before the first captured version, and two changes inside one 7-day backfill chunk.
  - The column descriptions of `fct_order_items.unit_cost_at_sale` and of `int_order_items__costed`.
  - The note of the gross-margin-by-brand example in `agent/mcp-server/src/sherpa_mcp/examples.yaml`: "costs are each line's cost at the time of sale".
  - `NOT_EXPOSED` in `test_reader_access.py` gets `("snapshots", "snap_product_variants")`.
- [ ] **Step 4:** `make lint && make test`. Commit `feat: product variants snapshot and cost at the time of sale`.

---

### Task 3: `sherpa-generate start-date`, the chunked backfill and `make advance`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Interfaces — Produces:**
- **`sherpa-generate start-date`** (`cli.py`, a Typer command named `start-date`): prints the simulation's `start_date` as `YYYY-MM-DD`, or nothing when no state exists. Exit 0 either way. Its errors are handled like the other commands'.
- **`data-platform/scripts/backfill.py`** (prints are allowed in scripts):
  - `CHUNK_DAYS = 7  # data platform spec §6.3`
  - `chunk_sizes(days: int, chunk_days: int = CHUNK_DAYS) -> list[int]`
  - `default_start_date(days: int, today: date) -> date`: returns `today - timedelta(days=days)`, so the last simulated day is yesterday.
  - `class BackfillError(Exception)`.
  - `class ChunkedBackfill(command: Callable[[Sequence[str]], str], snapshot: Callable[[], None])`, with `backfill(days, start_date, *, seed, scale) -> None`:
    - **Before generating anything:** `command(["sherpa-generate", "start-date"])`. Any output raises `BackfillError("a simulation already runs from <date>; make backfill starts a new one (make reset first), make advance DAYS=<n> continues it")`.
    - **Each chunk:** `command(["sherpa-generate", "generate", "--days", n, "--seed", seed, "--scale", scale] + (["--start-date", start] if first chunk))`, then `snapshot()`.
    - `command` runs the process, returns its stdout (which is echoed) and raises `BackfillError` on a non-zero exit.
  - **`DbtSnapshot(project_dir: Path, target: Literal["prod", "eval"])`:**
    - parses once with `dbtRunner().invoke(["parse", …])`
    - then each call runs `dbtRunner(manifest=…).invoke(["snapshot", "--target", target, "--target-path", f"target-{target}", "--project-dir", …, "--profiles-dir", …])`
    - raises `BackfillError` unless the result succeeded
    - **Why:** one parse instead of a fresh dbt process per chunk saves about 4 s × 53 chunks
  - **`main(argv) -> int`:**
    - argparse: `--days` (int ≥ 1, required), `--start-date` (default `default_start_date`), `--seed` 42, `--scale` 1.0, `--target` prod|eval
    - `BackfillError` → `error: <msg>` on stderr, exit 2
- **Makefile:**
  - **`backfill`:** `uv run --env-file .env python data-platform/scripts/backfill.py --days $(DAYS)`, with `$(DBT_PACKAGES)` as a prerequisite because the script runs `dbt snapshot`.
  - **`advance`** replaces `generate`, because the snapshot after it takes about a second. `DAYS` is required (`$(error …)` as `generate` had). It runs `uv run sherpa-generate generate --days $(DAYS)`, then `$(DBT) snapshot $(PROD)`, with `$(DBT_PACKAGES)` as a prerequisite.
  - **`eval-db`:** the generator line becomes `SHERPA_DB_NAME=$(SHERPA_EVAL_DB_NAME) uv run --env-file .env python data-platform/scripts/backfill.py --days 365 --start-date $(EVAL_START) --seed 42 --target eval`, with `EVAL_START = 2025-01-01`.
  - In `.PHONY`, `generate` becomes `advance`. Every mention of `make generate` follows (`git grep -n "make generate"`). That includes ui-1's freshness spec, which calls `make("generate", "DAYS=1")` in `ui/frontend/e2e/freshness.spec.ts`: it becomes `make("advance", "DAYS=1")`.

- [ ] **Step 1: Tests first:**
  - `data-platform/generator/tests/test_cli.py`:
    - `["start-date"]` joins `COMMANDS` (the missing-settings test)
    - `test_start_date_before_any_run_prints_nothing` (integration)
    - `test_start_date_prints_the_first_simulated_day` (after `generate --days 1 --start-date 2025-03-01`, stdout `2025-03-01\n`)
  - `data-platform/tests/scripts/test_backfill.py`, with fake `command`/`snapshot` collaborators that record calls:
    - `test_chunk_sizes_are_weeks_with_a_short_last_chunk`, parametrized: 365 → `[7] * 52 + [1]`, 14 → `[7, 7]`, 3 → `[3]`, 1 → `[1]`
    - `test_default_start_date_makes_the_last_day_yesterday`: 365 days on 2026-10-25 → 2025-10-25
    - `TestChunkedBackfill::test_passes_the_start_date_only_to_the_first_chunk_and_snapshots_after_each`: 15 days from 2025-01-01. The calls are probe, generate `--days 7 --seed 42 --scale 1.0 --start-date 2025-01-01`, snapshot, generate 7 (no start date), snapshot, generate 1, snapshot.
    - `test_refuses_a_simulation_that_has_already_started`: the probe returns `"2025-01-01\n"`. Expect `BackfillError` matching `make advance`, and no generate or snapshot call.
    - `test_stops_at_a_failing_chunk_without_snapshotting_it`: the second generate raises `BackfillError`; expect exactly one snapshot.
    - `test_main_rejects_zero_days` (`SystemExit` 2).

  Run them. Expected: FAIL.
- [ ] **Step 2:** Implement. `make lint && make test`. Expected: PASS.
- [ ] **Step 3: Verify on a scratch database.** Create `sherpa_dp3_check`, then:
  1. `SHERPA_DB_NAME=sherpa_dp3_check uv run --env-file .env python data-platform/scripts/backfill.py --days 15 --start-date $(date -u -d '-20 days' +%F)`. Expected: three chunk lines and three snapshots. `select count(*) - count(distinct id) from snapshots.snap_product_variants` > 0, which means the price history was captured.
  2. Run it again. Expected: exit 2 with the "already runs" message, and no rows added.
  3. `SHERPA_DB_NAME=sherpa_dp3_check make advance DAYS=2`. Expected: two days simulated and a snapshot.

  Drop the database.
- [ ] **Step 4:** Commit `feat: chunked backfill with snapshots, make advance and sherpa-generate start-date`.

---

### Task 4: Events load in microbatches

**Model:** implementer `sonnet` · reviewer `sonnet`

**Interfaces — Produces:**
- **`stg_events__events`** becomes
  ```
  {{ config(materialized='incremental', incremental_strategy='microbatch', unique_key='event_id',
            event_time='received_at', batch_size='day', lookback=3,
            begin=(env_var('SHERPA_SIMULATION_START_DATE', '') or var('date_spine_start')),
            on_schema_change='fail',
            indexes=[{'columns': ['event_id'], 'unique': True}, {'columns': ['received_at']}]) }}
  ```
  - dbt-postgres runs each batch as a MERGE on `unique_key`: the unique index serves it, and the existing `unique` test still guards R1.
  - The R1 dedupe stays inside the model, because duplicates share their `received_at` day and so their batch.
  - A comment says why `begin` comes from the environment: Postgres runs batches one after another, so `begin` must be the first simulated day, not an early fixed date.
- **The source `events.events`** gets `config: {event_time: received_at}`, so each batch reads only its day of the source.
- **The guard, singular `assert_every_source_event_is_staged.sql`:**
  - It returns `received_at` dates with their count of source `event_id`s missing from `stg_events__events`.
  - Its comment explains the cause: microbatch picks batches from the wall clock (today and the 3 days before). After advancing more than that, the oldest days are skipped.
  - The comment also gives the fix: `dbt build --select stg_events__events --event-time-start <first day> --event-time-end <last day + 1>`, then the normal build.
  - Because the test fails the build before `int_events__sessionized` runs, the sessions' watermark doesn't move past the missing events.
- **Makefile:**
  - `SIMULATION_START = $(shell uv run sherpa-generate start-date 2>/dev/null)`
  - `build build-prod: export SHERPA_SIMULATION_START_DATE = $(SIMULATION_START)`, recursive, so `make up` never runs it
  - in `eval-db`, `$(DBT) build $(EVAL) --event-time-start $(EVAL_START) --event-time-end $(EVAL_END)` with `EVAL_END = 2026-01-01`: the frozen year needs a frozen range

- [ ] **Step 1:** Write the guard test. Run `make build`. Expected: PASS while the model is still a view.
- [ ] **Step 2:** Implement the config, the source `event_time` and the Makefile lines.
- [ ] **Step 3: First microbatch build on the shared dev warehouse.** Run `dbt build --full-refresh --select stg_events__events+` (dev), with `SHERPA_SIMULATION_START_DATE` set to the output of `uv run sherpa-generate start-date`.
  - The model becomes a table, with one batch per day from the start date.
  - Note the time. Expected: under 3 minutes.
  - `select count(*) from dev_staging.stg_events__events` = `select count(distinct event_id) from raw_events.events`.
- [ ] **Step 4:** `make build` twice. Expected: each run logs 4 batches for `stg_events__events` (today and 3 days back), and the guard passes. `make lint && make test`. Commit `feat: events load in daily microbatches with a guard for skipped days`.

---

### Task 5: The device-to-customer map (`merge`)

**Model:** implementer `sonnet` · reviewer `sonnet`

**Interfaces — Produces:** `int_identity__anonymous_to_customer`.
- **Config:** incremental `merge`, `unique_key='anonymous_id'`, `on_schema_change='fail'`, unique index on `anonymous_id`.
- **Columns:** one row per `anonymous_id` that has ever been identified:
  - `customer_id`, `identified_at` (the `occurred_at` of the identifying event), `identifying_event_id`
  - `last_received_at`, the latest `received_at` among that device's identified events
- **Identified events:** `stg_events__events` rows with `customer_id` not null. Per device, the latest `occurred_at` wins; ties go to the highest `event_id`.
- **Incremental runs:**
  - New events are those with `received_at > coalesce(max(last_received_at) from {{ this }}, '-infinity')`.
  - Their per-device winner is compared with the device's existing row from `{{ this }}`, and the later `identified_at` wins. A late but older identification never overrides a newer one.
  - `last_received_at` = the greatest of both.
- **Test accounts stay in the map;** `fct_sessions` drops their sessions (Task 7).
- **YAML:** unique + not_null `anonymous_id`; not_null `customer_id`.

- [ ] **Step 1: Unit tests** (`_identity__models.yml`; `this` in `format: sql`):
  - **`…_maps_each_device_to_its_latest_customer`** (full build). Events (`event_id, anonymous_id, customer_id, occurred_at`):
    - e1 a1 101 10:00, e2 a1 101 11:00, e3 a2 null 10:30, e4 a3 103 09:00

    Expect a1 → 101 / e2 / 11:00, a3 → 103 / e4 / 09:00, and no a2.
  - **`…_keeps_the_latest_identification`** (`is_incremental` true):
    - `this`: (a1, 101, identified 2025-03-10 12:00, e5, last received 2025-03-10 12:00:05) and (a5, 105, 2025-03-09 10:00, e1, 2025-03-09 10:00:02).
    - New events, each listed as `event_id, anonymous_id, customer_id, occurred_at / received_at`:
      - e6 a1 102, 03-10 11:00 / 03-12 09:00 (late and older)
      - e7 a2 103, 03-11 10:00 / 03-11 10:00:03
      - e8 a3 null, 03-11 10:05 / 03-11 10:05:01
      - e11 a5 106, 03-11 09:00 / 03-11 09:00:02
      - e5 again, already processed
    - Expect (`anonymous_id, customer_id, identified_at, last_received_at`):
      - a1 → 101, 03-10 12:00, 03-12 09:00
      - a2 → 103, 03-11 10:00, 03-11 10:00:03
      - a5 → 106, 03-11 09:00, 03-11 09:00:02
  - **`…_reads_every_event_when_the_table_is_empty`:** an incremental run with an empty `this` (`where false`) and e1–e4. Expect the same rows as the full build.
- [ ] **Step 2:** Run them (FAIL) → implement → `make build` (PASS). `make lint`. Commit `feat: device-to-customer map, incremental with merge`.

---

### Task 6: Sessionization becomes incremental (`delete+insert`)

**Model:** implementer **`opus`** · reviewer **`opus`** (the watermark and cutoff arithmetic, sessions across midnight, and the late-event and empty-table edges)

**Interfaces — Produces:** `int_events__sessionized`, keeping its columns and rules (spec §6.4) and adding:
- **Config:** incremental `delete+insert`, `unique_key='session_date'`, `on_schema_change='fail'`, index on `session_date`, the existing `analyze` post-hook.
- **New columns `device_type` and `page_path`** (event level, from staging), which `fct_sessions` needs.
- **Incremental runs** (spec §6.4):
  1. `watermark = coalesce(max(max_received_at) from {{ this }}, '-infinity')`.
  2. `cutoff_date = (min(occurred_at) at time zone 'UTC')::date − 1`, taken over events with `received_at > watermark`.
  3. Read every event with `occurred_at >= (cutoff_date − 1)::timestamp at time zone 'UTC'`.
  4. Sessionize them as in a full build, and emit only sessions with `session_date >= cutoff_date`.
  5. With no new events, `cutoff_date` is null and the run emits nothing.
- **A full build** (`not is_incremental()`) reads everything, as today.

- [ ] **Step 1: Unit tests** (`_events__models.yml`; `stg_shop__customers` and `channel_mapping` `rows: []`; events on 2025-03-10 UTC unless stated; each `received_at` is 1 s after `occurred_at` unless stated):
  - **`…_starts_a_new_session_only_after_a_gap_over_the_timeout`:** a1 at 10:00, 10:29, 10:59 (exactly 30 min after the previous) and 11:30 (31 min). Expect `session_start_at` 10:00 for the first three and 11:30 for the fourth, all with `session_date` 2025-03-10.
  - **`…_keeps_a_session_across_midnight_on_its_first_day`:** 03-10 23:50 and 03-11 00:10. Both start at 03-10 23:50, with `session_date` 2025-03-10.
  - **`…_puts_a_late_event_into_its_existing_session`** (incremental):
    - `this` has `max_received_at` values 2025-03-07 08:00:01 and 2025-03-10 10:10:01.
    - Events: e0 03-07 08:00, e1 10:00, e2 10:10, and e3 at 10:20 received 2025-03-12 09:00.
    - Expect exactly e1, e2 and e3, with `session_start_at` 03-10 10:00 and `max_received_at` 03-12 09:00. e0 is not emitted: the cutoff is 03-09, so reading starts at 03-08.
  - **`…_emits_nothing_without_new_events`:** `this` max 03-10 10:10:01, and events e1 and e2 only. Expect `rows: []`.
  - **`…_reads_every_event_when_the_table_is_empty`:** an empty `this`, events e1 and e2. Expect both, with start 10:00.
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS).
- [ ] **Step 3: On the shared dev warehouse:**
  1. `dbt build --full-refresh --select int_events__sessionized+`. A plain `make build` must first fail with dbt's schema-change error (`on_schema_change='fail'`); check that it does.
  2. `make build` twice. Expected: PASS. The second run's sessionized step inserts the sessions of the cutoff days only, which shows in dbt's row count.
  3. The singular session tests still pass.
- [ ] **Step 4:** `make lint`. Commit `feat: incremental sessionization with delete+insert on session_date`.

---

### Task 7: `fct_sessions`, the conversion rate, and the agent's scope texts

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- Create `models/marts/marketing_web/fct_sessions.sql` and `_marketing_web__models.yml`.
- Create singular tests `assert_sessions_and_orders_agree.sql` and `assert_session_channels_are_mapped.sql`.
- Modify `assert_no_test_accounts_in_marts.sql`, `_metrics.md`, `agent/mcp-server/src/sherpa_mcp/tools.py`, and the tests listed in Step 4.

**Interfaces — Produces:** `fct_sessions`, one row per session (spec §6.5).
- **Inputs:** `int_events__sessionized`, `int_identity__anonymous_to_customer`, `stg_shop__customers`, `stg_marketing__campaigns`.
- **Columns (contract types):**
  - `session_id` text; `anonymous_id` text; `customer_id` bigint, stitched from the map by `anonymous_id`
  - `session_date` date; `session_start_at` and `session_end_at` timestamptz (the latter is the last `occurred_at`); `duration_seconds` integer
  - `event_count` and `page_view_count` integer
  - `landing_page_path` and `device_type`, from the first event (by `occurred_at`, `event_id`)
  - `utm_source`, `utm_medium`, `utm_campaign` (the session's); `marketing_channel`
  - `campaign_id` bigint: the campaign whose `utm_campaign` equals the session's, null if none
  - `is_first_session`: the visitor's first session. The visitor is the stitched customer when there is one, otherwise the device: `partition by customer_id, case when customer_id is null then anonymous_id end order by session_start_at, session_id`.
  - `viewed_product`, `added_to_cart`, `began_checkout`, `purchased` boolean (the session has that event type)
  - `order_id` bigint, from the purchase event
- **Excluded:** sessions whose stitched customer is a test account.
- **Config:** indexes on `session_date` and `customer_id`.
- **`meta`:**
  - `domain: marketing_web`
  - grain: one row per web session, i.e. one visitor's events with no gap over 30 minutes; app orders have no session
  - `primary_key: session_id`
  - synonyms: model `[visits, web sessions, traffic]`; `marketing_channel [channel, traffic source]`; `landing_page_path [landing page, entry page]`; `purchased [converted]`; `is_first_session [new visitor]`; `duration_seconds [session length, time on site]`
- **`metric_conversion_rate`:** sessions with `purchased` / all sessions. Sessions are web only, and app orders (35%, R12) have none. Referenced in the model description and on `purchased`.
- **Tests:**
  - relationships `customer_id` → `dim_customers`, `order_id` → `fct_orders`, `campaign_id` → `stg_marketing__campaigns`
  - `accepted_values` `device_type` [desktop, mobile, tablet] and `marketing_channel` [direct, organic, paid_search, paid_social, email, affiliate, referral, unknown]
  - `dbt_utils.expression_is_true`: `session_end_at >= session_start_at`, `page_view_count <= event_count`, `purchased = (order_id is not null)`
  - **`assert_sessions_and_orders_agree`:** every `fct_orders` row with a `session_id` has that session in `fct_sessions` with the same `order_id`, and every `fct_sessions.order_id` points to an order with that `session_id`
  - **`assert_session_channels_are_mapped`:** returns sessions with `marketing_channel = 'unknown'`, with `{{ config(severity='error', warn_if='>0', error_if='>100') }}`. Its comment: a few mean `channel_mapping` lacks a new UTM pair (add it); over 100 mean the tracking is broken.
  - `assert_no_test_accounts_in_marts` also checks `fct_sessions.customer_id`
- **MCP scope texts (`tools.py`):**
  - `DOMAIN_SUBJECTS["marketing_web"] = "web sessions: traffic source and channel, campaign, landing page, device and the browse-to-purchase funnel"`
  - `DOES_NOT_COVER` drops the web-sessions entry. The campaign entry ends "(customers, promotions and sessions carry only a campaign id)", and the comment above it names only data-platform-4's entries.
  - `NOTES` += "sessions are web only: app orders have no session, so conversion_rate and session counts leave app orders out"

- [ ] **Step 1: Unit tests on `fct_sessions`:**
  - **`fct_sessions_aggregates_a_session`.** Sessionized events of session `s2` (device a2, all on 2025-03-10):
    - page_view `/home` desktop 10:00
    - product_view 10:05
    - add_to_cart 10:06
    - begin_checkout 10:08
    - purchase 10:10 with order 9001

    Session `s3` (a2, 2025-03-11 09:00, one page_view). Map a2 → 202 (a non-test customer). Expect:
    - `s2`: `event_count` 5, `page_view_count` 1, `duration_seconds` 600, `landing_page_path` `/home`, `device_type` desktop, all four funnel flags true, `order_id` 9001, `is_first_session` true, `customer_id` 202
    - `s3`: `is_first_session` false
  - **`fct_sessions_drops_test_account_sessions`:** session `s1` of device a1 (events without a customer) and `s2` of a2. Map a1 → 201 (`is_test_account` true) and a2 → 202. Expect only `s2`.
- [ ] **Step 2:** Run (FAIL) → implement the model, YAML, doc block and singular tests → `make build` (PASS). `make lint`.
- [ ] **Step 3:** `uv run dbt show --inline "select marketing_channel, count(*), avg(purchased::int) from {{ ref('fct_sessions') }} group by 1 order by 2 desc"` (dev). Expected:
  - roughly the generator's channel mix
  - no `unknown`
  - overall conversion near 3.1% (spec §5.2)
- [ ] **Step 4: Tests that pin the mart list or the domains:**
  - `MARTS` in `agent/mcp-server/tests/test_examples.py` and in `test_reader_access.py` += `fct_sessions`.
  - In `agent/mcp-server/tests/test_tools.py`: the expected domains += `marketing_web`, the `covers` index of the sales line moves from 2 to 3, `test_list_tables_are_the_nine_marts` becomes `test_list_tables_are_the_marts`, and the `_expected_marts` docstring drops "nine".
  - ui-1's tests: `EXPECTED_MART_COUNT = 9` → 10 and `test_real_catalog_has_nine_marts` → `test_real_catalog_has_every_mart` in `ui/backend/tests/api/test_catalog_api.py`; `MARTS` += `fct_sessions` and the test title "tree lists the nine marts…" → "tree lists every mart…" in `ui/frontend/e2e/explorer.spec.ts`. Then `git grep -n -i -E "nine.?marts" -- ui agent data-platform` returns nothing.

  `make lint && make test`. Commit `feat: fct_sessions mart with the conversion rate, and the agent's scope texts`.

---

### Task 8: The incremental-vs-full-refresh analysis and the milestone acceptance

**Model:** implementer `sonnet` · reviewer `sonnet`

**Adds:** `analysis-paths: ["analyses"]` in `dbt_project.yml`; `analyses/fct_sessions_incremental_vs_full_refresh.sql`.
- It compares `{{ ref('fct_sessions') }}` (the current target) with `{{ api.Relation.create(database=target.database, schema=var('compare_schema'), identifier='fct_sessions') }}` using `except` both ways, and returns the differing rows with a `side` column.
- `var('compare_schema')` has no default, so a run that forgets it fails at compile time.

Every step below runs with `export SHERPA_DB_NAME=sherpa_dp3_check` on a freshly created scratch database. Dates are relative to today (UTC).

- [ ] **Step 1: Baseline:**
  1. `uv run --env-file .env python data-platform/scripts/backfill.py --days 30 --start-date $(date -u -d '-37 days' +%F)`. The simulation ends 8 days ago.
  2. `make build`: the first dev build, from the simulation's start.
  3. Record `select count(*) from raw_meta.pending_events` (> 0: late events are waiting).
- [ ] **Step 2: Review Focus 1, a catch-up beyond the lookback:**
  1. `make advance DAYS=4`, which simulates days −7 to −4.
  2. `make build`. Expected: FAIL on `assert_every_source_event_is_staged`, listing the four days, with `int_events__sessionized` skipped.
  3. `dbt build --select stg_events__events --event-time-start <day −7> --event-time-end <day −3>`, then `make build`. Expected: PASS.
- [ ] **Step 3: Spec acceptance:**
  1. `make advance DAYS=3`, which simulates days −3 to −1 and flushes pending late events.
  2. `make build` (dev, incremental). Expected: PASS.
  3. `dbt build --full-refresh --target prod --target-path target-prod`, with `SHERPA_SIMULATION_START_DATE` set from `sherpa-generate start-date`.
  4. `dbt show --select fct_sessions_incremental_vs_full_refresh --vars '{compare_schema: marts}'` on dev. Expected: 0 rows. The incremental history, which includes the late events and the targeted backfill, equals a full rebuild.
- [ ] **Step 4: `dbt retry`:**
  1. Introduce a typo in `fct_sessions.sql`, then `make build`. Expected: `fct_sessions` errors and its children are skipped.
  2. Fix the typo, then `dbt retry` (dev). Expected: only `fct_sessions` and the skipped nodes run. Keep the output for the guide.
- [ ] **Step 5: Reader access and the agent on the scratch prod build:** `make build-prod` (it adds nothing to the full refresh and writes `catalog.json`), then `uv run --env-file .env pytest -m warehouse data-platform/tests agent/mcp-server/tests`. Expected: PASS, including the new `NOT_EXPOSED` entries and `fct_sessions` in the mart lists. Drop `sherpa_dp3_check`.
- [ ] **Step 6: The frozen eval database:**
  1. Before running, record the fingerprint `select count(*), md5(string_agg(order_id::text || net_revenue::text, ',' order by order_id)) from marts.fct_orders` on the current `sherpa_eval`.
  2. `make eval-db`. Expected: completes. Its snapshot holds about a year of price changes. `stg_events__events` covers exactly the 365 frozen days.
  3. The fingerprint is unchanged: net revenue doesn't depend on cost. Run `make eval-db` once more and get the same fingerprint again.
- [ ] **Step 7:** `make lint`. Commit `feat: analysis proving incremental builds equal a full refresh`.

---

### Task 9: Documentation and how-to guides

**Model:** implementer `sonnet` · reviewer `haiku` (prose against commands already run; the reviewer checks references, not design)

All docs describe the current state only and follow `CLAUDE.md`'s guide rule:
- a basic guide stays under about 150 lines, an advanced one under about 250
- output appears only where reading it is the point, cut to about 10 lines
- link the README, the spec or another guide instead of repeating them
- errors go in the guide's symptom → fix table

This milestone extends the existing guides and adds none. Run every command once while writing (Task 8's outputs can be reused). When a guide would go over its limit, cut its least useful part.

- [ ] **Step 1: `basic/generator-cli.md`:**
  - `make advance DAYS=n` in place of `make generate`
  - `make backfill`: weekly chunks with a snapshot after each, how long it takes, new simulations only
  - `sherpa-generate start-date`
- [ ] **Step 2: `basic/building-the-warehouse.md`, a section "Keeping it current"** (about 40 lines):
  - how the three incremental models run (the batch lines in the log; the 3-day lookback counts from today)
  - price history: one variant's versions in `snapshots.snap_product_variants`
  - `--full-refresh`, and why snapshots ignore it
  - `dbt retry`
  - the incremental-vs-full-refresh check with the analysis

  **Elsewhere in the guide:**
  - unit tests run in every build and show an actual-vs-expected diff when they fail
  - stored failures live in `dev_test_failures`, and singular tests in `tests/singular/`
  - the error table gains "`assert_every_source_event_is_staged` fails after a long advance" → the targeted `--event-time-start/--event-time-end` command
- [ ] **Step 3: `basic/querying-the-marts.md`:**
  - `fct_sessions` in "The marts"
  - one query: conversion rate by channel
  - one trap: sessions are web only, and what the stitched `customer_id` and `is_first_session` mean
- [ ] **Step 4: `advanced/adding-or-changing-a-mart.md`, a section "Change an incremental model"** (about 50 lines):
  - the three strategies and why each model has its own
  - where each watermark lives
  - a unit test of incremental logic (`overrides: macros: {is_incremental: true}`; `this` and ephemeral inputs as `format: sql`)
  - `on_schema_change: fail`, and when a change needs `--full-refresh`
  - adding a snapshot column (history before it can't be rebuilt)
  - the analysis as the proof

  **Also:** the MCP guides' scope texts (`advanced/extending-the-mcp-server.md`, `basic/using-the-mcp-server.md`) as they now are. Check every touched guide is within its limit with `wc -l`.
- [ ] **Step 5: Update `data-platform/dbt/models/overview.md`:** snapshots, and the `marketing_web` domain.
- [ ] **Step 6: Update `README.md`:**
  - the status line: data-platform-3 is built
  - the Quickstart backfill time
  - the `make generate` line becomes `make advance`
  - the guide descriptions of the guides this milestone extends
- [ ] **Step 7: Update `CLAUDE.md`:**
  - Commands: `make advance` in place of `make generate`, `make backfill` (chunked, new simulations only), the `make reset` schemas, and `make build` reading the simulation start
  - Conventions: singular tests in `tests/singular/`, unit tests in the model YAML, incremental models with `on_schema_change: fail`
  - Structure: `data-platform/scripts/backfill.py`
  - the current-plan line
- [ ] **Step 8:** Check every command, path, model, test and Make target named in the touched docs exists (`git grep`, `ls`, `uv run dbt ls`). Expected: no dangling references.
- [ ] **Step 9:** Commit `docs: guides, README and CLAUDE.md for data-platform-3`.

---

### Task 10: Panel review

**Model:** the controller session runs this task itself, as `CLAUDE.md` describes, because it dispatches the reviewers. Every reviewer and the devil's advocate run on `opus`. Fixes: implementer `sonnet`; reviewer `opus` for fixes in the three incremental models or the backfill script, `sonnet` otherwise.

**The panel and each reviewer's focus:**
- **Data engineer:** the snapshot, the three incremental strategies, the microbatch wall-clock design, test coverage and severities.
- **Software engineer:** the backfill script, the `start-date` command, the Makefile and environment plumbing, the Python tests.
- **Analytics domain expert:** whether sessions, stitching, `is_first_session`, conversion rate and cost at sale mean what an e-commerce analyst expects.
- **Security engineer:** what `sherpa_agent` and `sherpa_ui` can reach (snapshots, `test_failures`, `fct_sessions`), and the scratch-database and environment-variable handling.
- **AI engineer:** the agent-facing interface: `fct_sessions` metadata and synonyms, the MCP scope texts and notes, the example's cost note. Would the agent answer session questions correctly from them?
- **Devil's advocate**, after the others.

No frontend reviewer: this milestone has no UI change.

- [ ] **Step 1: Run the panel** (the procedure is in `CLAUDE.md`):
  - every reviewer gets `git diff main...HEAD`, its focus above, this plan, and spec §6–§8 (the AI engineer also gets agent spec §4–§5)
  - the devil's advocate gets the diff and every finding
- [ ] **Step 2: Triage and fix** as `CLAUDE.md` describes.
- [ ] **Step 3: Re-verify:** `make build && make lint && make test`. If a fix touched an incremental model, the backfill script or the Makefile, re-run Task 8 Steps 1–3 on a fresh scratch database.
- [ ] **Step 4:** Commit the fixes (`fix: …`, one commit per finding or per group of closely related findings). The triage table goes into the PR description.
