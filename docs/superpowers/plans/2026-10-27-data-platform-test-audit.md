# Data Platform Test Audit — Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps the data-platform test audit found.
- The realism rules and the generator's rates get tested on the generator's own output, not only as pure functions.
- The backfill script's dbt side, the reader roles and the audit-hook test get the tests the spec implies.
- The test helpers and fixtures are consolidated.

When it's done, every one of the 14 one-line mutants that survived the audit fails the default suite.

**Architecture:** this is tests only, plus two spec sentences the owner chose to relax or align. One `Simulator`-driven month run replaces the test-only copy of the day loop. A small rate helper turns each spec rate into an assertion whose tolerance follows from the sample size. The shared builders move into `helpers.py`, and the cross-component tests get a `data-platform/tests/conftest.py`. No production code changes, apart from what a fixed test proves wrong (then a regression test comes first, per `CLAUDE.md`).

**Tech Stack:** pytest, psycopg, the generator's `Simulator`, dbt's `dbtRunner` (faked in the script tests).

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`: §3 (roles), §4.4 (realism rules R1–R12), §5.2 (daily simulation and its rates), §5.4 (acceptance), §6.3 (chunked backfill). This plan's commit relaxes §5.2's runtime ("about 5 minutes; the slow test allows 7") and aligns §5.4 with the stronger reference run (10 days, md5 over every row) and with the two-tests-per-rule rule. **`CLAUDE.md` is binding.**

**Source:** the read-only test audit of 2026-10-27, whose findings this plan turns into tasks. Its numbers:
- 407 tests, 99% branch coverage
- 14 one-line mutants that survived the default suite
- the full-year runtime test at 305 s against 300 s

**Base:** `main`. It runs in parallel with ui-3, whose files don't overlap (only `CLAUDE.md` does, in another section).

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 8's panel.

## Global Constraints

- **Tests only.** A change to `data-platform/generator/src/` or `data-platform/scripts/` is allowed only when a new test proves a bug: write that test first and watch it fail (`CLAUDE.md` regression rule). The dead `BaseModel` branch in `database/record_writer.py:110-119` is reported to the owner in the PR, not deleted.
- **The shared warehouse is read-only for tests.** Nothing writes to `sherpa` or `sherpa_eval`. Integration tests get a database from `temp_database` or a shared run fixture.
- **Shared run fixtures are read-only,** and their teardown compares a fingerprint, so a test that writes into one fails.
- **No magic tolerances.** A rate assertion uses the helper of Task 2 with the spec's rate; its bound comes from the trial count. A deliberately wide tolerance for a soft target (§5.2 calls them approximate) says so in a comment.
- **Spec values exactly** (`CLAUDE.md`): rates come from §4.4 and §5.2, each with its section in a comment.
- **Markers by need:** `integration` when Postgres is enough, `warehouse` only for built relations or artifacts.
- **Runtime:** the default suite is about 250 s today and must not get slower. The month fixture saves 11–17 s, which the new rate tests may spend.
- **Every commit** leaves `make lint` and `make test` green.

## Review Focus

1. **A flaky rate test:** a tolerance tight enough to catch a 10× change must not fail on a different seed or a small sample. The helper's bound is a `z = 4` binomial interval. A rule whose month sample is too small to separate its rate from 10× (or from 0) is measured on a larger dedicated run instead. Test: `TestAssertRate` (Task 2).
2. **The month fixture changing what's measured:** statistics that tests pin today (e.g. "measured 0.570") come from the copied loop. After the swap, each pinned value is re-measured and its comment updated, never loosened to pass. Test: Task 1 Step 2 lists every pinned value it re-measured.
3. **A dbt failure in the backfill script:** a parse or snapshot failure must stop the backfill with exit 2, and eval's artifacts must never land in `target-prod`. Tests: `TestDbtSnapshot` (Task 3).
4. **Another worktree's build:** the audit-hook test must read its own dbt invocation, not "the latest prod build" in the shared warehouse. Test: `test_reads_its_own_invocation` (Task 4).
5. **A mutant that still survives:** the acceptance re-runs all 14 mutants on a copy outside the repo. One that survives reopens its task. Step: Task 7.

---

## File Structure

```
data-platform/generator/tests/
  conftest.py               # one `month` Simulator fixture; fingerprint teardown for shared runs
  helpers.py                # record builders, shop_snapshot, run_days, db helpers, assert_rate
  simulation/test_generator_rates.py   # the rate table on the month run (new)
  behaviour/…               # rare-rule rate tests next to their simulator's tests
data-platform/tests/
  conftest.py               # WarehouseSettings, admin, dbt(*args)  (new)
  postgres/test_roles.py    # moved from generator/tests/database/test_roles.py, merged
  dbt/…, scripts/…
```

---

### Task 1: One month fixture through `Simulator`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `data-platform/generator/tests/conftest.py`, `helpers.py`, the tests that use `traffic_month`, `order_month` or `simulated_days`.

**Interfaces — Produces:**
- **`month`:** a session fixture, read-only, yielding the `Database` of one `Simulator` run of 30 days at scale 0.2 from 2025-03-01 (spec §5.4), seed `RUN_SEED`. Its teardown asserts that `fingerprint(db)` is unchanged.
- It replaces `traffic_month`, `order_month`, `simulated_days` and `_flush_pending_events` (the copy of `simulator.py:182-192`), which go away.

- [ ] **Step 1:** Write the fixture. Move every test that used the two old fixtures onto `month`.
- [ ] **Step 2:** Run the suite. For each test that pins a measured value or a tolerance, re-measure it on `month` and update the value and its comment. List them in the commit message as old → new. A tolerance may tighten; it may only widen with a written reason.
- [ ] **Step 3:** Add the same fingerprint teardown to `after_sales_season` and `simulator_reference`. `make test` (expected: no slower). Commit `test: one Simulator month run replaces the copied day loop`.

---

### Task 2: Rates on the generator's output

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `helpers.py` (`assert_rate`), `simulation/test_generator_rates.py` (new), additions in `behaviour/test_order_lifecycle.py`, `behaviour/test_after_sales.py`, `behaviour/test_visitor_traffic.py`, `behaviour/test_crm_activity.py`, and the full-year slow test.

**Interfaces — Produces:**
- **`assert_rate(hits: int, trials: int, expected: float, *, z: float = 4.0) -> None`:** fails when `hits / trials` lies outside `expected ± z·sqrt(expected·(1−expected)/trials)`.
  - The message names the rate, the bound and the counts.
  - It refuses (`AssertionError` "sample too small") when the bound can't exclude both 0 and 10× `expected`, so a test can't quietly measure nothing.
- **The rate table** in `test_generator_rates.py`: one parametrized `test_rule_rate_on_the_month(rule, sql, expected)` over `month`. Each row is a rule id, the SQL counting hits and trials on the raw tables, and the spec rate. Keep the rows the month's sample supports:
  - **R1:** duplicated events, 1%
  - **R2:** late events, 2%
  - **R3:** email noise on customers and on leads, 10% each
  - **R12:** app orders, 35%
  - **§5.2:** email opens 25% and clicks 3% of sends; devices per visitor between 1 and 3 (a bound, not a rate)
- **Rare rules,** on larger runs of the simulator that applies them, over synthetic snapshots (the `shop_snapshot` of Task 5, or a local builder until then). Each sample is large enough for `assert_rate`'s refusal not to trigger:
  - **R4** country noise 3%, and **R5** test accounts 0.5%, which also place orders: in `test_visitor_traffic.py`
  - **R7** failed-then-retried 3%, with each failed attempt before a capture of the same amount, and split payments 5%; **R8** stacked discounts 3%; never-paid 2%; cancelled-before-shipping 3% of paid: in `test_order_lifecycle.py`
  - **R9** return approval 90% and goodwill 1% of delivered orders: in `test_after_sales.py`, over about 6,000 synthetic orders
- **R6** in the full-year slow test (`test_full_year_volume.py`): no order and no event after a customer's `deleted_at`. The month is too short for a deletion.
- **The runtime limit** in that test: `MAX_RUNTIME_SECONDS = 420`, with the comment pointing to spec §5.2's "the slow test allows 7".

- [ ] **Step 1: Tests first** for the helper, `TestAssertRate`:
  - `test_passes_inside_the_bound`
  - `test_fails_outside_the_bound_with_the_counts`
  - `test_refuses_a_sample_too_small_to_tell_10x`
  - `test_refuses_zero_trials`
- [ ] **Step 2:** Write the rate tests. Each must pass on the current generator and fail under its audit mutant: M1–M7, M10 and M12–M14 for these rows. Check each by applying the mutant to a copy outside the repo (see Task 7). `make test`. Commit `test: realism rule and generator rates measured on the generator's output`.

---

### Task 3: The backfill script's dbt side

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `data-platform/tests/scripts/test_backfill.py`, `data-platform/tests/scripts/test_check_docs_coverage.py`.

- [ ] **Step 1:** **`TestDbtSnapshot`**, with `dbtRunner` monkeypatched to a fake that records its invocations and returns a configurable result:
  - `test_parses_once_and_snapshots_per_call`
  - `test_target_path_follows_the_target` (parametrized: prod → `target-prod`, eval → `target-eval`; mutant M8)
  - `test_a_failed_parse_raises_backfill_error`
  - `test_a_failed_snapshot_raises_backfill_error`

  **`TestChunkedBackfill`:** `test_a_failing_snapshot_stops_the_chunks`. **`main`:** `test_main_passes_the_target_to_the_snapshot` and `test_main_exits_2_on_a_dbt_failure`.
- [ ] **Step 2:** `check_docs_coverage`:
  - `test_ignores_nodes_that_are_not_models` (mutant M9)
  - `test_a_sibling_prefix_is_not_under_the_path` (`models/stagingx` against `models/staging`)
  - `test_missing_catalog_is_an_error_message`
- [ ] **Step 3:** `make test`. Commit `test: backfill dbt snapshot and docs-coverage edge cases`.

---

### Task 4: Roles and the warehouse tests

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `git mv data-platform/generator/tests/database/test_roles.py data-platform/tests/postgres/test_roles.py`; `data-platform/tests/conftest.py` (new); `data-platform/tests/dbt/test_reader_access.py`, `test_run_results_audit.py`, `test_snapshot_time.py`.

**Interfaces — Produces** (`data-platform/tests/conftest.py`):
- **`WarehouseSettings`:** the role logins × main and eval database, read with pydantic-settings, not `os.environ`.
- **`admin`:** an admin connection fixture.
- **`dbt(*args) -> dbtRunnerResult`:** runs dbt in `data-platform/dbt` with the profiles directory. It replaces `AdminSettings`, the duplicated `DBT_DIR`/`ENV_FILE`/`_dbt`, and `test_roles._connect`.

- [ ] **Step 1: The roles tests** (`postgres/test_roles.py`, marker `integration`):
  - Merge in `TestRoleSettings` from `test_reader_access.py:207-234`. It needs only Postgres, so it runs by default.
  - Replace the two shallow tests with `test_a_reader_cannot_create_even_with_read_only_off`: set `default_transaction_read_only = off` in the session, then `CREATE TABLE` must raise `InsufficientPrivilege`.
  - Add `test_readers_cannot_connect_to_the_postgres_database`, parametrized over `sherpa_agent` and `sherpa_ui` (spec §3).
  - Add `test_large_object_functions_are_revoked`, parametrized over all six functions: `lo_create`, `lo_creat`, `lo_from_bytea`, `lo_open`, `lowrite`, `lo_put`.
- [ ] **Step 2: The warehouse tests:**
  - `test_reader_access.py` derives `MARTS` from `target-prod/manifest.json` (the models in schema `marts`) instead of a hard-coded tuple.
  - `test_run_results_audit.py` takes its `invocation_id` from the `run_results.json` of the dbt run it started (its own target path), in `test_reads_its_own_invocation`.
- [ ] **Step 3:** `make test` and `uv run --env-file .env pytest -m warehouse data-platform/tests` (after `make build-prod` in this checkout). Commit `test: roles tests by what they need, all large-object revokes, and the audit test's own invocation`.

---

### Task 5: Shared builders and helpers

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `data-platform/generator/tests/helpers.py` and the test files that carry local copies.

- [ ] **Step 1: Move into `helpers.py`:**
  - **Snapshots:** `catalog_variants(day)` (cached `CatalogBuilder` run) and `shop_snapshot(day, *, customers=50, **fields)`, as the audit sketched. They replace the three hand-built 20-field `StoreSnapshot`s (`behaviour/test_order_lifecycle.py:540`, `test_visitor_traffic.py:260`, `test_crm_activity.py:172`).
  - **Record builders:** `make_order`, `make_payment`, `make_item`, `make_event` and `make_campaign`, as `(id, **overrides)`. They replace about ten local copies.
  - **`run_days(simulate_day, snapshot, first, days)`** replaces the day loops in the after-sales, catalog-drift, customer-deletion and ad-spend tests.
  - **Database helpers:** `fetch_all`, `scalar`, `stored_state`, `assert_empty` and `MONTH_END_AT`. They replace `_rows` ×2, `_scalar`, `row_count` and three `END_OF_RUN` copies.
  - **The deletion tests:** a module fixture replaces their four recomputations of the same 10,000-customer run (about 17 s saved).
- [ ] **Step 2:** Where a test asserted a builder's hidden default (`"placed"`, `NOW` in `helpers.py:99-114` and `database/test_record_writer.py:98`), pass the value at the call site and assert that.
- [ ] **Step 3:** `make test` (expected: faster). Commit `test: shared record and snapshot builders, run_days and database helpers`.

---

### Task 6: Tidy-up

**Model:** implementer `haiku` · reviewer `sonnet`

- [ ] **Step 1: Delete the shallow tests** the audit lists:
  - `records/test_records.py:42`; at `:79-82`, keep the `extra="forbid"` half
  - `simulation/test_calendar.py:83`
  - `simulation/test_realism_rules.py:132`
  - `simulation/test_store_snapshot.py:201`
  - `behaviour/test_catalog_setup.py:196`
  - `test_cli.py:123`
- [ ] **Step 2: Merge or parametrize the duplicates:**
  - `simulation/test_simulator.py:123/129` and `135/146`
  - `simulation/test_simulation_state.py:33/42`
  - `data-platform/tests/dbt/test_seeds.py:13`, subsumed by `:23`
  - `database/test_raw_schema.py:20` vs `:81`
  - `test_cli.py:196-198` vs `simulation/test_simulator.py:268`
  - `simulation/test_deterministic_random.py:27/50`: one subprocess instead of four interpreters

  Rename the misleading `simulation/test_store_snapshot.py:165` to `…_is_empty`.
- [ ] **Step 3:** `make test`. Commit `test: drop shallow tests, merge duplicates`.

---

### Task 7: Acceptance: the mutants

**Model:** implementer `sonnet` · reviewer `sonnet`

- [ ] **Step 1: Apply each surviving mutant, one at a time, to a copy outside the repo.** Copy `data-platform/generator/src` and `data-platform/scripts` into a temp directory, edit the copy, and run the default suite with that copy first on `PYTHONPATH`. Each mutant keeps its random draw where possible, so the random stream is unchanged:

  | Id | Change |
  |---|---|
  | M1/M2 | R3 removed: `visitor_traffic.py:357`, `crm_activity.py:90` → `noisy_email(...).strip().lower()` |
  | M3 | R4 removed: `visitor_traffic.py:360,374` → `noisy_country(...) and session.country` |
  | M4 | R5 removed: `visitor_traffic.py:353` → `is_test_account(...) and False` |
  | M5/M6 | R7 rates: `order_lifecycle.py:298`/`:301` → 50% |
  | M7 | R8: `order_lifecycle.py:260` → every discounted order stacks a coupon |
  | M8 | `backfill.py:64` → `"target-prod"` for every target |
  | M9 | `check_docs_coverage.py:29` → the `resource_type` filter dropped |
  | M10 | `visitor_traffic.py:105` → device cap 3 becomes 30 |
  | M12–M14 | click 3%→20%, never-paid 2%→20%, goodwill 1%→5% |
  | M18 | `catalog_setup.py:321` → `is_single_use=True` |

  The line numbers are from the audit; find the same statement if they moved. M18 needs a test: "no coupon code is single-use" (the known simplifications under spec §5.4), on `month`. Add it here.
- [ ] **Step 2:** Run the controls the audit used: a mutant that is already caught must still fail. Every mutant above must fail the default suite (`make test`), not only the slow one. A survivor reopens its task. Record the table, all killed, for the PR description. Confirm `git status` shows no stray file. Commit any test added here: `test: kill the audit's surviving mutants`.

---

### Task 8: Documentation and panel review

**Model:** docs implementer `sonnet` · reviewer `haiku`. The panel runs as `CLAUDE.md` describes, every reviewer on `opus`, by the controller.

- [ ] **Step 1: Docs.**
  - **`CLAUDE.md` "Tests":** add the audit's rules, one line each:
    - shared builders in `helpers.py` as `make_<record>(id, **overrides)`; assert only values passed at the call site
    - snapshots only through `make_snapshot`/`shop_snapshot`
    - parametrize when arrange and act are shared
    - arrange over about 1 s that tests only read becomes a module or session fixture, read-only, with a fingerprint teardown
    - statistics run through `Simulator`, never a copy of its loop
    - each realism rule gets a pure test and a rate test on generator output (`assert_rate`)
    - nothing writes to `sherpa` or `sherpa_eval`; a dbt-running test identifies its own invocation
    - mark by need (`integration` vs `warehouse`); `data-platform/postgres/*.sql` tests live in `data-platform/tests/postgres/`
    - don't test a constant against what defines it
  - **`advanced/extending-the-source-data-model.md`:** "Add or tune a realism rule" names the two tests and `assert_rate`.

  Commit `docs: testing rules from the data-platform test audit`.
- [ ] **Step 2: Panel review.** Every reviewer gets `git diff main...HEAD`, its focus, this plan and spec §4.4, §5.2, §5.4:
  - **Data engineer:** the rate SQL against the spec's definitions (what counts as a trial and a hit for each rule), the tolerances, and what the moved roles and warehouse tests prove.
  - **Software engineer:** the fixtures and their teardown, the helpers' shape, the markers, the runtime, and anything that hides what a test does.
  - **Devil's advocate:** which mutant-like regressions would still pass (e.g. a rate halved rather than ×10), and whether any new test is shallow itself.
- [ ] **Step 3:** Triage, fix, re-verify (`make lint`, `make test`, the slow suite once, the warehouse suite once). The triage table and the mutant table go in the PR description, with the dead `record_writer` branch noted for the owner.
