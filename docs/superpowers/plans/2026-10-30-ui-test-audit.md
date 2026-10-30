# UI Test Audit — Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps the UI test audit found: tests that never run, limits nothing asserts, mocks that have drifted from the API, flaky timing, and above all a browser whose sort and filter disagree with Postgres in seven cases and are compared by no test. When it is done, every surviving mutant of the audit fails the suite, and one shared case file keeps the browser and the server in step.

**Architecture:**
- **Tests** are fixed in place: collection and speed first, then the missing assertions and the fixtures.
- **The parity gap** is closed by one shared case file that the backend (through the real server wrapper on Postgres) and Vitest (through the browser's code) both run. The browser's code gets a small policy change: exact digits for numbers, Postgres's reading of a date filter, and the server for column types it cannot reproduce.
- **No other production code changes.** A fix to production code starts with a failing test (`CLAUDE.md`).

**Tech Stack:** pytest, psycopg, Vitest + Testing Library, Playwright, the generated API types in `ui/frontend/src/api/schema.d.ts`.

**Spec:** `docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md`: §4 (errors), §5 (sort and filter), §6.4 (Explorer), §8 (testing). This plan's commit writes the parity policy into §5, the one exception to the error shape into §4, and the test-layer rows of §8. **`CLAUDE.md` is binding.**

**Source:** the read-only test audit of 2026-10-30: backend 323 tests at 98% coverage (35 of 40 mutants caught), Vitest 235 tests (42 of 54 caught), Playwright 24 tests (not run, because `ui` and `mcp` were down).

**Base:** `main`. **Order against ui-3:** Tasks 1, 2 and 4 touch files ui-3 does not. Tasks 3, 5 and 6 touch the lineage tests that ui-3 rewrites from its Task 3 on, so run them after ui-3's Task 3 and rebase onto it first.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 7's panel.

## Global Constraints

- **Tests first for production changes** (`CLAUDE.md` regression rule): the only production code this plan changes is `ui/frontend/src/components/resultView.ts` and its callers (Task 4), and `pyproject.toml`'s pytest config (Task 1).
- **The shared warehouse is read-only for tests.** The parity test runs `values` queries only. Nothing writes to `sherpa` or `sherpa_eval`.
- **Other sessions share the machine:** a test that runs while another suite runs must still pass. Timeouts are set in config, not left to library defaults.
- **A worktree has none of the gitignored files** (`CLAUDE.md`, Environment notes): copy `.env`, run `uv sync`, `cd ui/frontend && npm ci`, and `make build-prod` once for the artifacts.
- **Every commit** leaves `make lint`, `make test` and `cd ui/frontend && npm test` green.

## Review Focus

1. **A parity case that encodes a wrong expectation:** a case's expected rows must come from running the query on Postgres, not from the browser's code. The case file is generated once from real Postgres output and then reviewed, and the backend test recomputes it. Test: `test_every_case_matches_postgres` (Task 4).
2. **The policy hiding a bug by sending everything to the server:** the server route is for the named column types only. A test asserts that number, text, date, timestamp and boolean columns still sort and filter in the browser without a new request. Test: `a complete result is sorted in the browser for the exact types` (Task 4).
3. **A collection fix that regresses:** a test directory named like one of pytest's skipped defaults must fail a check, not silently lose its tests. Test: `test_no_test_directory_is_skipped_by_pytest_defaults` (Task 1).
4. **Flakiness under load:** the Vitest suite runs three times in a row while the backend suite runs. Step: Task 1.
5. **A typed fixture that is only nominally typed:** a builder returning `Partial<…>` or `any` defeats the point. The builders return the full schema type, and a test file that passes a literal where a builder exists fails review. Step: Task 3.

---

## File Structure

```
pyproject.toml                                  # explicit norecursedirs
ui/parity/sort_filter_cases.json                # one case file, run by both suites
ui/backend/tests/{conftest.py, test_layout.py, readonly/test_sort_filter_parity.py, …}
ui/frontend/tests/fixtures/{api.ts, render.tsx} # typed builders and the render helper
ui/frontend/tests/resultViewParity.test.ts
ui/frontend/src/components/resultView.ts        # exact digits, date part, needsServer
ui/frontend/vite.config.ts, tests/setup.ts      # timeouts, restoreMocks, jsdom stub
ui/frontend/e2e/{lineage,explorer,offline}.spec.ts, e2e/helpers.ts
```

---

### Task 1: Collection and speed

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `pyproject.toml`, `ui/backend/tests/test_layout.py` (new), `ui/backend/tests/api/test_health_api.py`, `ui/backend/tests/readonly/test_query_runner.py`, `ui/frontend/vite.config.ts`, `ui/frontend/tests/setup.ts`, the Vitest files with sleeps or real timers.

- [ ] **Step 1: Tests first.**
  - `test_layout.py::test_no_test_directory_is_skipped_by_pytest_defaults`: the effective `norecursedirs` (`request.config.getini`) does not skip any directory under `ui/backend/tests` (it fails today, because `build` is a default).
  - `test_layout.py::test_every_test_file_is_collected`: walk `ui/backend/tests` for `test_*.py` and compare with the files pytest collected in this session (`request.session.items`' paths, from a session-scope fixture). Run with the default addopts; it fails today with `build/test_build_status.py`.
- [ ] **Step 2: Implement.**
  - `pyproject.toml`: set `norecursedirs` explicitly to pytest's defaults without `build` (`*.egg`, `.*`, `_darcs`, `CVS`, `dist`, `node_modules`, `venv`, `{arch}`). Comment why. The 7 build-status tests now run and must pass; fix any that have rotted.
  - `test_health_api.py` (3 tests): simulate the unreachable database by monkeypatching `psycopg.connect` to raise `OperationalError`, not by connecting to port 1 (30 s today).
  - `test_query_runner.py`: the wide-result test (1,000 × 1,000 cells, 6.4 s) and the real 0.4 s delays use a finite fake cursor and an injectable clock or event.
  - Vitest: in `vite.config.ts` set `testTimeout` (20 s) and `restoreMocks: true`; in `setup.ts` call Testing Library's `configure({ asyncUtilTimeout: 5000 })` and stub `Range.prototype.getClientRects` (CodeMirror logs a TypeError in jsdom). Mock `layoutGraph` with `vi.mock` in every test that is not about layout, so no component test runs the real ELK. Replace the fixed sleeps with fake timers or a positive signal: `LineageScreen.test.tsx:174,179`; the 1 s real retry delay in `queries.test.tsx:49`; `ExplorerScreen.test.tsx:275,283` (unrestored `matchMedia`) and the exact render count at `:324` become a bound.
- [ ] **Step 3: Verify.** `uv run --env-file .env pytest ui/backend --co -q | grep build_status` lists the tests. The backend suite is at least 30 s faster (record before and after). `cd ui/frontend && npm test` passes three times in a row while `pytest ui/backend` runs in another terminal (Review Focus 4). Commit `test: collect the build-status tests, fix the slow and flaky ones`.

---

### Task 2: Backend gaps and tidy-up

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `ui/backend/tests/`, `ui/backend/src/sherpa_ui/` only if a new test proves a bug.

- [ ] **Step 1: The missing assertions** (each must fail under its audit mutant):
  - **C2:** `test_query_runner.py::test_sets_the_statement_timeout_of_the_settings`: the fake connection's `execute` received `set local statement_timeout = '30s'`, and `'1s'` when `ui_statement_timeout_s=1` (mutant B03: `"0"`).
  - **`WARNING_CODES`:** parametrize the logging test over every code in it, asserting a warning record for each (mutant: `timeout` dropped).
  - **Nested JSON integers** above 2^53 are left as they are (`readonly/bounded_result.py:91`): one test.
  - **The largest valid request** (~600,000 bytes of SQL, spec §4) is posted and accepted; today's tests are relative to `MAX_BODY_BYTES`.
  - **"Shared last":** the catalog fixture gets a domain that sorts after `shared`, so the test proves the rule (mutant B31).
  - **Trusted host:** `test_app.py:38` asserts the plain-text 400, as the spec now states.
  - **Warehouse** (`warehouse`, read-only): one test each for `/api/health`, `/api/catalog/tables/{name}`, `/api/queries/format` and `/api/queries/{id}/cancel` (an unknown id answers 204).
- [ ] **Step 2: Delete and merge:**
  - **Delete the shallow tests:** `test_settings.py:51` (pydantic-settings reads an env variable), `lineage/selectors.test.ts:16` (a template literal), `AppShell.test.tsx:31` (a change detector for the nav items), `HealthBanner.test.tsx:49` (Mantine's CSS variable names).
  - **Merge the duplicates:** `lineage/test_node_status.py:166` into `:112`; `api/test_queries_api.py:125` into its parametrized row; `ResultGrid.test.tsx:44` into `resultView.test.ts:15`; `LineageScreen.test.tsx:126` into its own `it.each`.
  - **Fix the misleading test:** `api/test_queries_api.py:133` claims to prove the in-run fallback but its mock returns the warning, so it proves nothing; rewrite it to drive the real fallback or delete it.
- [ ] **Step 3: Fixtures:**
  - `REPO_ROOT` (4 copies), `LONG_AGGREGATE` (3, one in e2e) and the two `LastFullBuild` builders move to `conftest.py` or one helper module.
  - One `offline_settings` fixture whose connect raises.
  - The environment dict of `test_app.py` becomes a `ui_env` fixture.
  - `_arrange_exit_path`'s string switch becomes small parametrized arrange functions.
- [ ] **Step 4:** `make test`, plus `uv run --env-file .env pytest -m warehouse ui/backend`. Commit `test: backend timeout, warning-code and size assertions; fewer duplicates and fixtures`.

---

### Task 3: Typed Vitest fixtures and the render helper

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `ui/frontend/tests/fixtures/api.ts` and `render.tsx` (new), and the test files that build payloads by hand: `Explorer`, `ExplorerScreen`, `ExplorerResults`, `SqlEditor`, `ResultGrid`, `useWorksheetRun`, `HealthBanner`, `AppShell`, `queries`, `lineage/*`.

**Interfaces — Produces:**
- **`api.ts`:** builders that return the full generated schema type (`components["schemas"][…]`), each taking `Partial<…>` overrides over complete defaults: `queryResponse`, `catalog`, `table`, `lineageNode`, `lineageGraph`, `nodeDetails`, `health`, `apiFailure(code, message, status, position?)`, and `ok(data)`.
- **`render.tsx`:** `renderWithProviders(ui, { path?, queryClient? })`: Mantine `env="test"`, `MemoryRouter`, a `QueryClientProvider` with retries off. It also exports the one `deferred()`, one `firstColumn()`, the editor lookups and the `wideScreen` stub that today exist in several copies.

- [ ] **Step 1:** Write the builders. A deliberate missing field in a test must fail `npm run typecheck`: add a type-level check (`// @ts-expect-error` on a literal missing `selector`).
- [ ] **Step 2:** Migrate every file that passes a literal to a mocked `api`, hook or component to the builders. This fixes the drift the audit found: `LineageScreen.test.tsx` and `SelectorBar.test.tsx` omit the required `selector`, `materialization`, `status` and `failing_tests`; `HealthBanner` has a hand-written `Health` type.
- [ ] **Step 3:** Run `npm test` and `npm run typecheck`. A review sweep: `git grep -n "mockResolvedValue({"` in `ui/frontend/tests` returns nothing. Commit `test: typed API builders and one render helper for the Vitest suite`.

---

### Task 4: The sort/filter parity test and policy

**Model:** implementer `sonnet` · reviewer **`opus`** (Postgres's input coercions, exact decimal comparison and the routing rule are where it goes subtly wrong)

**Files:** `ui/parity/sort_filter_cases.json` (new), `ui/backend/tests/readonly/test_sort_filter_parity.py` (new), `ui/frontend/tests/resultViewParity.test.ts` (new), `ui/frontend/src/components/resultView.ts` and its callers (`ExplorerResults`/`ResultGrid`, where `isServerSide` is read).

**The case file:** `{ "tables": [ … ], "cases": [ … ] }`.
- **A table** has: `name`; `sql` (a `select * from (values …) as v(…)` over literal rows, no relation touched); `column_types` and `rows` exactly as the API returns them in client mode.
- **A case** has: `table`, `sort` and/or `filters` (by column index, as the grid sends them), `expected_ids` (the first column of each row, in order), and optionally `route: "server"` (the browser must send this view to the server, so only the backend runs it) or `accepted_difference` plus `client_expected_ids` and a reason.
- **Coverage:** every operator and both directions on every column type the grid knows:
  - **number:** int4, int8 including values above 2^53, numeric, float8 including `1e15` and `1e-5`
  - **text:** case, `é`, an emoji, `～`, `ß`
  - **date, timestamp, boolean**
  - **other:** uuid (upper-case), interval, json, jsonb
  - **NULLs and ties** in each

  The hostile and boundary filter values: `2025-01-15 10:30` on a date column, `+02` offsets, upper-case uuids, `t`, `of`, `yes`, spaces, `1e3`, `NaN`, `2025-02-30`.

**The policy** (UI spec §5): exact digits for `number`; a date column's filter value is read by its date part; any sort or filter on an `other` column, and `contains` on a `number` column, goes to the server; the naive-`timestamp` offset is the one accepted difference.

- [ ] **Step 1: Generate the case file from Postgres.** Write the tables first, run each case's server query through `build_wrapped_query` on the real database as `sherpa_ui` (read-only `values` queries) and record the ids it returns as `expected_ids`. Review the file by hand against Postgres's documented input rules; it is the reference.
- [ ] **Step 2: Tests first.**
  - **Backend,** marker `integration`: `test_every_case_matches_postgres` runs every case's wrapped query and compares to `expected_ids`; `test_the_tables_are_what_the_api_returns` runs each table's `sql` through `QueryRunner` in client mode and asserts the `rows` and `column_types` equal the file (pinning the API's form).
  - **Vitest:** `resultViewParity.test.ts` runs `applyView` over every table's rows and types for each case without `route: "server"` and compares to `expected_ids` (or `client_expected_ids` for an accepted difference). `needsServer(view, types)` is asserted `true` for each `route: "server"` case and `false` for every other case.
  - Run them. Expected: the Vitest test FAILS for the six known disagreements below and passes for the rest, including the seventh, which is the recorded accepted difference.
- [ ] **Step 3: Implement** in `resultView.ts`:
  - **Exact digits** (int8 above 2^53, numeric): compare two numeric strings as digits (integers through `BigInt`, decimals by aligned digit strings). Keep `Number` for values that arrive as JSON numbers within the safe range.
  - **Date part:** for a `date` column, `compareValues` reads a filter value's date part when it has a time.
  - **`needsServer(view, types)`:** `true` when a sort or filter touches an `other` column, or a `contains` filter touches a `number` column. `isServerSide` returns it for a complete result, so the grid re-runs the SQL with sort and filters exactly as for a truncated one (duplicate column names keep today's fallback and banner).
  - **Accepted:** the naive-`timestamp` offset, recorded as `accepted_difference` in the case file with its reason.
  - The seven disagreements the audit confirmed (six fixed here, the last accepted), each by a case: a date column against `2025-01-15 10:30`; int8 above 2^53; interval sorting; an upper-case uuid; float8 `contains`; json `contains`; and a naive `timestamp` against a value with an offset (the accepted one).
  - Also pin the NULL-text `!=`/`<` case (it already matches Postgres: the audit's mutant F13 survived only for lack of a test).
- [ ] **Step 4:** Add the test `a complete result is sorted in the browser for the exact types` (no new request) and `an interval column is sorted on the server` (Review Focus 2). `make lint`, `make test`, `npm test`. Commit `test: sort and filter parity between the browser and Postgres, and the policy it needed`.

---

### Task 5: Frontend gaps from the surviving mutants

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** the Vitest files next to each unit.

- [ ] **Step 1: Tests first**, each failing under its audit mutant:
  - `api/queries.ts`: `staleTime` is at most 30 s and data refetches on window focus (F18b, F18c), asserted through the query client's defaults.
  - `LineageCanvas`: the initial fit never zooms past 1× (F26), and the minimap appears above the node threshold and not below (F27).
  - `useGraphLayout`: a superseded layout that fails keeps the new positions (F07b).
  - `url.ts`: an empty `node=` is no selection (F08).
  - `resultView.ts`: invalid dates and times are refused, `2025-02-30` and `10:75` (F16b, F16c); the boolean word `of` (F15); UTF-16 against code-point order for an emoji (F38).
  - Spec §7: the Explorer's stored state never holds result rows (serialise the state after a run and assert no cell value is present).
  - Focus returns to the node after a background click clears the selection; the tree's "Copy name" copies the table name.
- [ ] **Step 2:** `npm test` (three runs). Commit `test: pin the frontend limits and details the audit's mutants showed`.

---

### Task 6: End-to-end robustness

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `ui/frontend/e2e/lineage.spec.ts`, `explorer.spec.ts`, `offline.spec.ts`, `layout.spec.ts`, `warehouse.ts`, and a new `helpers.ts`.

- [ ] **Step 1:**
  - `lineage.spec.ts:71-73,161`: read the expected status of `fct_orders` and whether a build is recorded from `ops.dbt_run_results` through `adminScalar`, instead of asserting "ok", so the spec holds after a real warning or failing build.
  - `explorer.spec.ts:88-101`: the cancel check looks for the cancelled statement by its own query text in `pg_stat_activity`, not by counting every `sherpa_ui` session.
  - `helpers.ts::blockForeignRequests(page, baseURL)`, used by every offline case (it is copy-pasted today).
  - The `MARTS` list comes from `GET /api/catalog` at run time, not a hard-coded copy of the data platform's list.
  - Drive the Table-path depth controls once end to end (the audit found them undriven).
- [ ] **Step 2: Run the whole suite** once: `make up && make build-prod`, then `make e2e`. The audit could not run Playwright because `ui` and `mcp` were down. Record the result and fix what it shows. The two opt-in specs stay opt-in. Commit `test: end-to-end specs read their expectations from the warehouse`.

---

### Task 7: Documentation and panel review

**Model:** docs implementer `sonnet` · reviewer `haiku`. The panel runs as `CLAUDE.md` describes, every reviewer on `opus`, by the controller.

- [ ] **Step 1: Docs.**
  - **`CLAUDE.md`, Tests:** add the audit's UI rules, one line each:
    - no test directory is named like a pytest skipped default (`build`, `dist`, …); a test checks the effective config
    - every Vitest API payload comes from a typed builder over the generated schema; no literal passed to a mocked `api`
    - sort and filter semantics live in the one shared case file that pytest and Vitest both run; a new operator or column type adds cases first
    - a value sent to Postgres (`set local`, session options) is asserted on the fake connection in the default suite
    - every limit or default the spec names has a behaviour test that fails when the value changes
    - an unreachable database is simulated by making `psycopg.connect` raise, never by connecting to a closed port
    - no sleep proves that something did not happen: fake timers or a positive signal
    - Vitest restores every spy after each test (config), sets its timeouts explicitly, and mocks ELK outside layout tests
    - assert what the user sees or what goes over the wire, not a render count or a library's CSS variable
    - e2e specs read expected values from the warehouse at run time
    - a fixture must be able to tell the rule it tests apart from the default
  - **`advanced/extending-the-ui.md`:** the test layers section gains the shared case file and how to add a case.
  - **`basic/using-the-explorer.md`:** one symptom → fix row: sorting an interval/uuid/json column or filtering a number with "contains" re-runs the query on the server.
  - Commit `docs: UI testing rules from the test audit`.
- [ ] **Step 2: Panel review.** Every reviewer gets `git diff main...HEAD`, its focus, this plan and UI spec §4, §5, §6.4, §8:
  - **Frontend engineer:** the typed builders and render helper, flakiness under load, the e2e specs, the routing to the server and what the user sees when it happens.
  - **Software engineer:** the config and collection checks, the backend fixtures, test speed, the markers.
  - **Data engineer:** the case file against Postgres's real coercion rules (dates, int8, numeric text, uuid, interval, json, naive timestamps), and the exactness of the browser's comparison.
  - **Devil's advocate:** which regressions would still pass (a policy that sends too much, or too little, to the server; a case file that mirrors the code instead of Postgres).
- [ ] **Step 3:** Triage, fix, re-verify (`make lint`, `make test`, `npm test` three times, `make e2e`). The triage table and the table of the audit's mutants, all now killed, go in the PR description.
