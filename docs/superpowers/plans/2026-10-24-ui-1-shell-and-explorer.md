# ui-1 — Shell and Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The first slice of the web app: a dockerized FastAPI + React app whose Explorer screen browses the marts with their dbt documentation and runs read-only SQL as `sherpa_ui`. It brings the data-platform pieces the UI needs: the `sherpa_ui` role, its grants, and the `ops.dbt_run_results` audit hook.

**Architecture:**
- **Backend (`sherpa_ui`):**
  - reuses `sherpa_mcp`'s pure modules: `MartCatalogLoader` for the catalog, `SqlGuard` and `PiiMasker`
  - adds a `QueryRunner` with the UI's own limits (30 s timeout, 1,000-row cap, cancel, server-side sort/filter)
  - exposes them through FastAPI routers
- **Frontend:** a Vite + React + TypeScript SPA with Mantine, calling the API through a typed client generated from the backend's OpenAPI schema.
- **Image:** one `ui` image builds the SPA in a Node stage and serves it from FastAPI.

**Tech Stack:**
- **Frontend:** Node 22+ / npm; React 18+, TypeScript, Vite; Mantine (core, hooks, notifications); TanStack Query; `openapi-typescript` + `openapi-fetch`; CodeMirror 6 (`@uiw/react-codemirror`, `@codemirror/lang-sql`); Mantine React Table *or* TanStack Table + Mantine `Table` + TanStack Virtual (see Task 7); `@tabler/icons-react`; ESLint; Vitest + Testing Library; Playwright.
- **Backend:** FastAPI + uvicorn, sqlglot, psycopg 3.

**Spec:** `docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md`: §1 (criteria), §2, §3, §4, §5, §6.1, §6.4, §7, §8, §9 and the ui-1 row of §10. Data platform spec §3 (roles) and §8 ("Added by ui-1"). Agent spec §4.2 (the guard and masking this reuses). **`CLAUDE.md` is binding.**

**Needs on `main` first:** data-platform-2, and **agent-design-1** (`sherpa_mcp.sql_guard`, `masking` and `catalog`, with `Mart.tests` and `MartColumn.metric_refs`).

**Model selection:** every task names its implementer and reviewer models below. Dispatch exactly those. Fix rounds 4–5 go one tier up (`haiku` → `sonnet` → `opus`), and the **final whole-branch review runs on `opus`**. A dispatch that names no model is a mistake: it falls back to the project default (`sonnet`, in `.claude/settings.json`), which is wrong for every task the plan puts on `opus` or `haiku`.

## Global Constraints

- **Incremental commits (CLAUDE.md):** each task adds only the files, packages, config, Make targets, `.env` variables and `.gitignore` entries it uses. Every commit leaves `make lint` and `make test` green.
- **Layout:** `ui/backend/src/sherpa_ui/` plus `ui/backend/tests/`; `ui/frontend/` (its own `package.json` + `package-lock.json`); `ui/Dockerfile`, built with the **repository root as context**.
- **Python packaging:** one root `pyproject.toml`; dependency group `ui` (`fastapi`, `uvicorn[standard]`); `packages` += `ui/backend/src/sherpa_ui`; `testpaths`, mypy `files` += the UI backend.
- **Read-only path** (UI spec §5): role `sherpa_ui`; connection `options` `-c default_transaction_read_only=on -c statement_timeout=30s`; `SET LOCAL statement_timeout = '30s'` per transaction; `SqlGuard` on every user query; **row cap 1,000** (fetch 1,001); PII masked with `PiiMasker`.
- **Bound to `127.0.0.1:${SHERPA_UI_PORT:-8090}`** with `TrustedHostMiddleware(allowed_hosts=["localhost", "127.0.0.1"])`. One uvicorn worker.
- **Offline:** no CDN, external font or remote asset anywhere; Mantine uses the system font stack.
- **Browser storage keys** start with `sherpa.v1.`. A corrupted or other-version entry is reset, not crashed on.
- **Tests:**
  - backend integration tests are marker `warehouse` (need `make build-prod`)
  - frontend unit tests run with `npm test` (Vitest)
  - Playwright runs with `make e2e` against the running stack
  - `make lint` adds `eslint`, `tsc --noEmit` and the API-client check

## Review Focus

1. **Two runs racing in one worksheet** (Run, then Run again before the first returns): the first is cancelled, and its late response must never overwrite the second's result. Test: Vitest `useWorksheetRun ignores a superseded response` (Task 7).
2. **Sorting numbers that arrive as strings** (`Decimal` values are JSON strings): client-side sorting must sort by the column's type (numeric, date, text), never lexicographically. Test: Vitest `ResultGrid sorts numeric strings numerically` (Task 8). It relies on the backend's `column_types` (Task 3).
3. **Empty and odd results:** zero rows; zero columns (`select from marts.fct_orders limit 1`); very long text; `NULL` everywhere. The grid shows them without crashing. Tests: `TestQueryRunner::test_zero_rows_and_zero_columns` (Task 3) and Vitest `ResultGrid renders empty and null cells` (Task 8).
4. **Corrupted browser storage** (invalid JSON, or a `sherpa.v0.` / unknown shape): the Explorer starts with a fresh worksheet and logs a warning. Test: Vitest `worksheetStore recovers from corrupted storage` (Task 7).
5. **Cancelling an unknown or finished `query_id`, or one from before a backend restart:** a no-op `204`, never an error. Test: `TestQueriesApi::test_cancel_unknown_id_is_noop` (Task 4).

---

## File Structure

```
data-platform/postgres/roles.sql                    # idempotent role SQL (all three roles)
data-platform/postgres/init/00_roles.sh             # now runs roles.sql
data-platform/dbt/macros/grant_read_access.sql      # replaces grant_agent_access.sql
data-platform/dbt/macros/record_run_results.sql     # the audit hook
data-platform/tests/dbt/test_reader_access.py       # replaces test_agent_access.py (agent + ui roles)
data-platform/tests/dbt/test_run_results_audit.py
ui/
├── Dockerfile
├── backend/
│   ├── src/sherpa_ui/
│   │   ├── __init__.py  settings.py  app.py
│   │   ├── readonly/__init__.py  query_runner.py  sort_filter.py
│   │   ├── catalog/__init__.py  catalog_service.py
│   │   ├── health/__init__.py  health_service.py
│   │   ├── api/__init__.py  models.py  health.py  catalog.py  queries.py
│   │   └── openapi_export.py            # writes the OpenAPI JSON for the frontend client
│   └── tests/   (mirrors the package)
└── frontend/
    ├── package.json  package-lock.json  vite.config.ts  tsconfig.json  eslint.config.js  index.html  playwright.config.ts
    ├── src/
    │   ├── main.tsx
    │   ├── app/AppShell.tsx  HealthBanner.tsx  routes.tsx
    │   ├── api/schema.d.ts (generated)  client.ts  queries.ts
    │   ├── screens/explorer/ExplorerScreen.tsx  CatalogTree.tsx  Inspector.tsx  WorksheetTabs.tsx  QueryHistory.tsx
    │   │                    useWorksheetRun.ts  worksheetStore.ts
    │   └── components/SqlEditor.tsx  ResultGrid.tsx
    └── tests/  (Vitest *.test.tsx next to screens/components is also fine) and e2e/explorer.spec.ts  e2e/offline.spec.ts
```

---

### Task 1: The `sherpa_ui` role, idempotent role script and `make roles`

**Model:** implementer `sonnet` · reviewer `sonnet` (shell and idempotent SQL, fully specified)

**Adds tooling:**
- `data-platform/postgres/roles.sql` and `large_objects.sql`, reached in the container through one new compose volume line `./data-platform/postgres:/sherpa:ro` (a folder, so Docker never creates a missing file as a directory). They are not in `/docker-entrypoint-initdb.d`, where they would run on their own without their variables.
- compose `postgres` env += `SHERPA_UI_USER`, `SHERPA_UI_PASSWORD`
- `.env.example` += `SHERPA_UI_USER=sherpa_ui`, `SHERPA_UI_PASSWORD=change-me`
- Makefile target **`roles`**

**Interfaces — Produces:**
- `roles.sql` takes the psql variables `admin_user`, `admin_password`, `agent_user`, `agent_password`, `ui_user`, `ui_password`, `db_name`. For each role it:
  - creates the role when missing and otherwise resets its password (`SELECT format(...) … \gexec`, so a re-run never fails)
  - applies every role setting of today's init script to the agent unchanged (`CONNECTION LIMIT 20`, `default_transaction_read_only = on`, `statement_timeout = '5s'`, `temp_file_limit = '256MB'`, with the explanatory comments), and the same shape to the UI role: `CONNECTION LIMIT 10`, `default_transaction_read_only = on`, `statement_timeout = '30s'`, `temp_file_limit = '256MB'`. `ALTER ROLE … CONNECTION LIMIT` and `ALTER ROLE … SET` are idempotent.
  - repeats the `PUBLIC` revokes of today's init script. They're idempotent statements.
- `00_roles.sh` runs `psql … -f /sherpa/roles.sql` with the variables, then runs `/sherpa/large_objects.sql` in `$POSTGRES_DB` and in `template1` as today.
- **`make roles`:** `docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -v admin_user="$$SHERPA_ADMIN_USER" … -f /sherpa/roles.sql'`, plus `large_objects.sql` in both databases. It reuses the init script's logic, so `00_roles.sh` and `make roles` can't drift.

- [ ] **Step 1:** Test first. In `data-platform/tests/dbt/test_reader_access.py` (rename `test_agent_access.py` with `git mv`), add `test_ui_role_settings` (marker `warehouse`): as admin, `pg_db_role_setting` for `sherpa_ui` holds `default_transaction_read_only=on`, `statement_timeout=30s` and `temp_file_limit=256MB`, `pg_roles.rolconnlimit` is 10, and the agent's settings are unchanged (`statement_timeout=5s`, `temp_file_limit=256MB`, limit 20). Run it. Expected: FAIL (no role).
- [ ] **Step 2:** Implement `roles.sql` and `large_objects.sql`, update `00_roles.sh`, compose (the new env vars recreate the `postgres` container on the same volume; the data stays), `.env.example`, and add the Make target. Run `make roles` on the **existing** volume. Expected: `sherpa_ui` exists, and the warehouse data is untouched (`select count(*) from marts.fct_orders` unchanged as admin).
- [ ] **Step 3:** Run `make roles` a second time. Expected: exit 0 and the same settings, which proves it is idempotent. Re-run the test. Expected: PASS.
- [ ] **Step 4:** On a throwaway volume (`docker compose -p sherpa-tmp …` with a temporary volume name; delete it afterwards), check that the init script alone creates all three roles. Commit `feat: sherpa_ui role with an idempotent roles script and make roles`.

---

### Task 2: Read grants for both roles, and the run-results audit hook

**Model:** implementer `sonnet` · reviewer `sonnet` (dbt Jinja macros against documented context variables)

**Adds tooling:**
- macros `grant_read_access` (replaces `grant_agent_access`) and `record_run_results`
- `dbt_project.yml`:
  - `on-run-end: ["{{ record_run_results(results) }}", "{{ grant_read_access() }}"]`, in this order, so the audit table exists when it is granted
  - marts `+grants: select:` lists both role names (`env_var` with defaults)
- Makefile: `grant-agent` becomes **`grant-read`**; `build-prod` adds `$(DBT) docs generate $(PROD)`; `DBT_SCHEMAS` += `ops`.

**Interfaces — Produces:**
- **`grant_read_access()`:**
  - on targets `prod` and `eval` (skipped elsewhere, logged as today), for **each** of `SHERPA_AGENT_USER` and `SHERPA_UI_USER`, validated with `validate_identifier` as today: `usage on schema marts, extensions` and `select on all tables in schema marts`
  - additionally, for the UI role only: `usage on schema ops` and `select on ops.dbt_run_results`
  - it still runs its SQL itself and returns `''`
- **`record_run_results(results)`:** runs on every target. It creates `ops` and `ops.dbt_run_results` if missing (columns exactly as data platform spec §8, "Added by ui-1"), then inserts one row per result:
  - `invocation_id` (from the `invocation_id` context variable), `target_name`, `command` (from `flags.WHICH`)
  - `is_full_run`: `audit_is_full_run(invocation_args_dict)`, true when it has no `select`, `exclude`, `selector`, `resource_types` or `exclude_resource_types`, and no `empty` or `sample`
  - `unique_id`, `resource_type`, `status`, `failures`, `execution_time_s`, `message`, `run_started_at`
  - values escaped with `adapter.quote`/literals, never string concatenation of untrusted text without escaping. `message` is truncated to 2,000 characters.

- [ ] **Step 1: Tests first:**
  - In `test_reader_access.py`, parametrize the existing read and deny tests over `role ∈ {agent, ui}` (the settings class gains `ui_user`/`ui_password`).
  - Add `test_ui_reads_run_results_and_agent_does_not`.
  - Create `test_run_results_audit.py` (marker `warehouse`):
    - `test_full_prod_build_records_every_built_node_once`: the latest full prod build has exactly one row, with `is_full_run`, per model (not ephemeral), seed, snapshot, test and unit test in the manifest
    - `test_a_selected_build_is_not_full`: after `dbt build --select <one data test of dim_date> --target prod --target-path target-scratch` (it reads the warehouse and rebuilds nothing), the newest invocation has `is_full_run = false` and that test's row only
    - `test_a_run_limited_to_a_resource_type_is_not_full`: the same after `dbt test --resource-type test`
    - `test_an_empty_run_is_not_full` and `test_which_invocations_are_full_runs`: `audit_is_full_run` rendered by one `dbt compile --empty --inline`, for that compile's own arguments and for an argument dict per flag

  Run them. Expected: FAIL.
- [ ] **Step 2:** Implement, then `make build-prod`. Re-run the tests. Expected: PASS. Also `make docs-check` and `make lint`.
- [ ] **Step 3:** In the same commit, rename every mention of `make grant-agent` / `grant_agent_access` to `make grant-read` / `grant_read_access`: data platform spec §8 (data-platform-2 features, "Added by ui-1" acceptance), `docs/guides/basic/building-the-warehouse.md` ("Hooks"), `docs/guides/advanced/adding-or-changing-a-mart.md`, and `CLAUDE.md`'s Commands. Also the macro file name (`git mv` to `grant_read_access.sql`), the Makefile `.PHONY` line and the `log(...)` prefixes inside the macro. The docs then never describe a missing target: `git grep -n -E "grant-agent|grant_agent_access" -- . ':!docs/superpowers/plans'` returns nothing (finished plans keep their history). Commit `feat: read grants for the agent and UI roles, and the dbt run-results audit hook`.

---

### Task 3: Backend core — settings, query runner, sort and filter

**Model:** implementer **`opus`** · reviewer **`opus`** (cancellation across threads, server-side cursors, error-position arithmetic: concurrency and subtle correctness)

**Adds tooling:** dependency group `ui` (`fastapi`, `uvicorn[standard]`) in `default-groups`; `pyproject.toml` package, testpaths and mypy entries; `.env.example` += `SHERPA_UI_PORT=8090`.

**Files:** `settings.py`, `readonly/query_runner.py`, `readonly/sort_filter.py`, tests.

**Interfaces — Consumes** (agent-design-1, merged; import them, don't copy them):
- `sherpa_mcp.catalog.MartCatalogLoader(target_path).load() -> MartCatalog`, which re-reads the dbt artifacts when either file changes and raises `CatalogUnavailable` when they are missing.
- `MartCatalog.mart_columns()` and `.pii_columns()`.
- `sherpa_mcp.sql_guard.SqlGuard(mart_columns).check(sql) -> GuardResult(ok, errors, tables, clock_calls)`. The default function allow-list is the UI's too (UI spec §5). Errors read `[guard:<rule>] …` and carry no position.
- `sherpa_mcp.masking.PiiMasker(pii_columns).mask(columns, rows)`, which masks by column name.
- `sherpa_mcp.database.json_safe(value)` for the JSON-safe conversion of cell values (`Decimal` → string, dates → ISO).
- `QueryRunner` calls `loader.load()` on every `run` and builds the `SqlGuard` and `PiiMasker` from that catalog, so a rebuilt warehouse is picked up without a restart (criterion 2).

**Produces:**
- `UiSettings(BaseSettings)`: the same `model_config` as `McpSettings` (frozen, prefix `SHERPA_`, `.env`, `extra="ignore"`).
  - Fields: `db_host`, `db_port`, `db_name`, `ui_user`, `ui_password: SecretStr`, `ui_statement_timeout_s: int = 30`, `ui_row_cap: int = 1000`, `ui_dbt_target_path: Path = Path("data-platform/dbt/target-prod")` (so the variable is `SHERPA_UI_DBT_TARGET_PATH`, UI spec §9, and never collides with the MCP server's `SHERPA_DBT_TARGET_PATH`).
  - `ui_conninfo() -> str`.
- `SortSpec(column, direction: Literal["asc", "desc"])` and `FilterSpec(column, operator: Literal["=", "!=", "<", "<=", ">", ">=", "contains", "is_null", "is_not_null"], value: str | None)` (frozen).
- `build_wrapped_query(sql_text: str, result_columns: Sequence[str], sort, filters) -> tuple[sql.Composed, list[object], int]`:
  - returns the wrapped query, its parameters, and the **prefix length** in characters that precede the user's SQL
  - the wrapper is `select * from (\n<sql>\n) as q where … order by …`; trailing `;` and whitespace are stripped first
  - columns must be in `result_columns`, otherwise `ValueError`; `contains` → `q.<col>::text ilike '%' || %s || '%'`
  - raises `DuplicateColumnsError` when `result_columns` has duplicates
- `QueryOutcome` (frozen): `query_id`, `columns`, `column_types: tuple[Literal["number", "text", "date", "timestamp", "boolean", "other"], ...]` (from the cursor description's type OIDs), `rows` (JSON-safe, masked), `returned_rows`, `truncated`, `elapsed_ms`, `mode: Literal["client", "server"]`, `warning: str | None`.
- `QueryRunner(settings, loader)`:
  - `run(query_id: str, sql_text: str, sort=(), filters=()) -> QueryOutcome`
  - `cancel(query_id) -> None`, idempotent
  - `format(sql_text) -> str`, via `sqlglot.transpile(..., read="postgres", write="postgres", pretty=True)`
  - **Errors** raise `UiQueryError(code, message, position: int | None)`:
    - `code` ∈ `guard_rejected`, `sql_error`, `timeout`, `cancelled`, `duplicate_query_id`, `catalog_unavailable`
    - `position` is mapped back to the user's SQL by subtracting the `DECLARE … CURSOR FOR ` prefix and, in server mode, the wrapper prefix; `None` if it falls outside
  - `run` in server mode (sort or filters given) first runs `select * from (<sql>\n) as q limit 0` to learn the result columns; on `DuplicateColumnsError` it runs the SQL in client mode on the same connection, under the same `query_id` (so a cancel is never lost between two runs), and sets `warning = "duplicate column names: sorting and filtering apply only to the rows shown"`
  - the running-queries registry is a `dict[str, psycopg.Connection]` behind a lock; `cancel` calls `conn.cancel_safe()`
  - a duplicate running `query_id` raises `duplicate_query_id`

- [ ] **Step 1: Write failing tests:**
  - **unit**, `test_sort_filter.py`:
    - `test_wrapper_strips_semicolon_and_survives_trailing_comment`
    - `test_unknown_column_rejected`
    - `test_contains_is_a_bound_parameter`
    - `test_duplicate_columns_raise`
    - `test_prefix_length_matches_text_before_user_sql`
  - **`warehouse`**, `test_query_runner.py`, class `TestQueryRunner`:
    - `test_runs_and_caps_at_1000` (`select * from marts.fct_order_items` → 1,000 rows, `truncated`)
    - `test_guard_rejection_has_code_and_reason`
    - `test_sql_error_position_points_into_user_sql`: `select nope from marts.fct_orders`, where the position equals the index of `nope`
    - `test_server_mode_sorts_all_rows`: sorting `fct_order_items` by `order_item_id desc` in server mode returns the maximum id first, as an admin query confirms
    - `test_server_mode_filter`
    - `test_server_mode_with_trailing_comment`
    - `test_duplicate_columns_fall_back_to_client`: the run comes back in client mode with the warning
    - `test_cancel_stops_a_long_query`: run an aggregate over a self cross join of `fct_order_items` in a thread, cancel after 1 s, `code == "cancelled"` within 3 s
    - `test_timeout`: with `ui_statement_timeout_s=1`, `code == "timeout"`
    - `test_pii_masked`
    - `test_zero_rows_and_zero_columns`
    - `test_column_types_reported`
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → `make lint`. Commit `feat: UI read-only query runner with sort, filter and cancel`.

---

### Task 4: Backend API — app, health, catalog, queries, OpenAPI export

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `app.py`, `catalog/catalog_service.py`, `health/health_service.py`, `api/*.py`, `openapi_export.py`, tests.

**Interfaces — Produces** (UI spec §4; every response a Pydantic model; every error `{code, message, detail?}`):
- `create_app(settings: UiSettings, static_dir: Path | None) -> FastAPI`:
  - `TrustedHostMiddleware`
  - routers under `/api`
  - when `static_dir` exists: static files plus an **SPA fallback** (any GET outside `/api` and `/assets` returns `index.html`, with `Cache-Control: no-cache`; a missing file under `/assets` is a JSON 404)
  - `/api/*` unknown routes return a JSON 404
- **`GET /api/health`:** `{database: ok|down, artifacts: {status: ok|missing, generated_at}}`. Only these two in ui-1; the tool server and model come with ui-4.
- **`GET /api/catalog`:** the tree database → `marts` → domain → tables → columns (UI spec §4).
  - `CatalogService(loader)` maps `MartCatalog` into API models.
  - Domains are sorted alphabetically, with `shared` last; tables alphabetically.
- **`GET /api/catalog/tables/{name}`:** Inspector details, including `tests` and each column's `metric_refs` with the metric definitions' text.
- **`POST /api/queries`:** `{query_id, sql, sort, filters}` → `QueryOutcome`.
  - The runner's duplicate-columns fallback reaches the client as `warning`; the endpoint never runs the query twice.
  - The endpoint watches `request.is_disconnected()` (poll every 0.5 s in a task) and cancels on disconnect.
  - Status codes: `422` for `guard_rejected`/`sql_error`, `504` for `timeout` (not `408`, which Chromium silently re-sends on a reused connection), `409` for `duplicate_query_id` and for `cancelled`, `503` for `catalog_unavailable` (also what the catalog endpoints answer when the dbt artifacts are missing). Clients branch on the body's `code`, never on the status alone.
- **`POST /api/queries/{query_id}/cancel`:** `204` always.
- **`POST /api/queries/format`:** `{sql}` → `{sql}`.
- **`python -m sherpa_ui.openapi_export <path>`** writes the app's OpenAPI JSON.

- [ ] **Step 1: Write failing tests:**
  - **unit** (FastAPI `TestClient`, `QueryRunner`/loader faked):
    - `TestHealthApi::test_artifacts_missing_reported`
    - `TestCatalogApi::test_missing_artifacts_is_503`
    - `TestCatalogApi::test_tree_groups_by_domain_shared_last`
    - `TestQueriesApi::test_cancel_unknown_id_is_noop`
    - `TestQueriesApi::test_duplicate_query_id_is_409`
    - `TestQueriesApi::test_duplicate_columns_fall_back_with_warning`
    - `TestApp::test_untrusted_host_rejected` (`Host: evil.example` → 400)
    - `TestApp::test_spa_fallback_and_api_404`
  - **`warehouse`**:
    - `TestCatalogApi::test_real_catalog_has_nine_marts`
    - `TestQueriesApi::test_end_to_end_query`
    - `TestQueriesApi::test_disconnect_cancels`: a client that closes mid-request leaves no running query in the registry within 2 s
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → lint. Commit `feat: UI API (health, catalog, queries) with SPA serving`.

---

### Task 5: Frontend scaffold, typed client and the app shell

**Model:** implementer `sonnet` · reviewer `sonnet`

**Adds tooling:**
- `ui/frontend/` with `package.json` (scripts `dev`, `build`, `test`, `lint`, `typecheck`, `e2e`), `package-lock.json`, Vite, TS and ESLint config, `index.html`
- `.gitignore` += `ui/frontend/node_modules/`, `ui/frontend/dist/`, `ui/frontend/test-results/`, `ui/frontend/playwright-report/`
- Makefile:
  - **`ui-client`**: export OpenAPI to a temp file, then `npx openapi-typescript` → `src/api/schema.d.ts`
  - **`ui-dev`**: runs `ui-api-dev` (`uv run --env-file .env uvicorn sherpa_ui.app:dev_app --reload --port $(UI_PORT)`) and `ui-web-dev` (`cd ui/frontend && SHERPA_UI_PORT=$(UI_PORT) npm run dev`, with the Vite dev server proxying `/api` to that port) via `$(MAKE) -j2`; `UI_PORT` is `SHERPA_UI_PORT` from the environment or the command line, else from `.env`, else 8090
- `make lint` += `cd ui/frontend && npm run lint && npm run typecheck`, plus the client check: regenerate into a temp file and `diff` it with the committed one.
- `sherpa_ui.app:dev_app`: a module-level app built from `UiSettings()` with no static dir.

**Interfaces — Produces:**
- `src/api/client.ts`: `api = createClient<paths>({ baseUrl: "" })` (openapi-fetch).
- `src/api/queries.ts`: TanStack Query hooks `useHealth()`, `useCatalog()`, `useTableDetails(name)`. `staleTime` 30 s; `refetchOnWindowFocus: true`.
- `AppShell`:
  - a Mantine `AppShell` with a left nav rail listing **only the screens that exist** (Explorer in ui-1)
  - routes from `routes.tsx` (`/explorer`; `/` redirects there until ui-3 adds Home)
  - light/dark theme from the OS
- `HealthBanner`: a banner per down dependency from `useHealth()`.

- [ ] **Step 1:** Scaffold by hand (no generator output you don't use), install the pinned versions, and run `make ui-client`. Check the Mantine React Table release against the installed Mantine major. If it doesn't support it, use TanStack Table + Mantine `Table` + TanStack Virtual instead (UI spec §2), and say which in the commit message.
- [ ] **Step 2: Vitest:**
  - `AppShell shows only existing screens`
  - `HealthBanner shows one banner per down dependency and none when healthy`

  Run them (FAIL), implement, run (PASS).
- [ ] **Step 3:** `make lint` (Python + ESLint + `tsc` + client check) and `npm test` pass. Commit `feat: UI frontend scaffold, typed API client and app shell`.

---

### Task 6: Catalog tree and Inspector

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `screens/explorer/CatalogTree.tsx`, `Inspector.tsx`, `ExplorerScreen.tsx` (layout: tree | editor + results | Inspector), tests.

Behaviour (UI spec §6.4):
- **Tree:** `sherpa › marts › <domain> › table › column`. Columns show type, **PK** and **PII** badges. A search box filters on table and column names, case-insensitive, and keeps parents of matches visible.
- **Hover** on a table or column: a tooltip with its description, plus grain and PK for tables.
- **Click:** opens the Inspector with `useTableDetails`: description, grain, PK, domain, columns with types and badges, accepted values, synonyms, the metric definitions a column uses, the table's tests, and "Open in Lineage". That button is disabled with the tooltip "Lineage arrives with ui-2"; the incremental rule allows no dead links.
- **Table menu** (right-click and a "⋯" button): Preview 100 rows, Insert SELECT, Copy name, Open in Lineage (disabled as above). The editor actions are wired in Task 7.

- [ ] **Step 1: Vitest** (mocked API):
  - `CatalogTree groups by domain and shows badges`
  - `search keeps parents of matching columns`
  - `hover shows the description`
  - `menu opens from right-click and from the ⋯ button`
  - `Inspector shows accepted values, synonyms, metric definitions and tests`

  Run (FAIL) → implement → run (PASS). Commit `feat: Explorer catalog tree and Inspector`.

---

### Task 7: SQL editor, worksheets, run and cancel

**Model:** implementer `sonnet` · reviewer **`opus`** (the superseded-response race in `useWorksheetRun` is the subtle part)

**Files:** `components/SqlEditor.tsx`, `screens/explorer/WorksheetTabs.tsx`, `QueryHistory.tsx`, `useWorksheetRun.ts`, `worksheetStore.ts`, tests.

**Interfaces — Produces:**
- `worksheetStore`: `localStorage` key `sherpa.v1.explorer`, holding `{worksheets: [{id, name, sql}], activeId, history: [{sql, ranAt, returnedRows, status}]}` (last 200 history entries). It validates on load; anything invalid is reset to one empty worksheet with a `console.warn`.
- `useWorksheetRun(worksheetId)`:
  - `run(sql, {sort?, filters?})` generates `query_id = crypto.randomUUID()`, POSTs it, and **cancels the previous `query_id` of the same worksheet first**
  - it keeps only the latest request's response (compare ids), so a late response is ignored
  - `cancel()` and `status: idle | running | done | error | cancelled`
- `SqlEditor`: CodeMirror with SQL highlighting and **autocomplete** fed from `useCatalog()` (`marts.<table>` and the active table's columns). Ctrl/Cmd+Enter runs, **Run selection** runs the selection, and **Format** calls the format endpoint. A `position` from an error is highlighted; for Run selection, the selection's start offset is added.
- **While running,** Run becomes **Cancel**, and the elapsed time is shown. A cancelled run shows "Cancelled after N s".

- [ ] **Step 1: Vitest:**
  - `useWorksheetRun ignores a superseded response`
  - `a new run cancels the previous query_id`
  - `Run becomes Cancel while running`
  - `error position is highlighted, offset by the selection`
  - `worksheetStore recovers from corrupted storage`
  - `history keeps the last 200`
  - `autocomplete offers marts tables and columns`

  Run (FAIL) → implement → run (PASS). Commit `feat: Explorer SQL editor, worksheets, run and cancel`.

---

### Task 8: Result grid with client and server sorting and filtering

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `components/ResultGrid.tsx`, tests; wiring in `ExplorerScreen.tsx`.

Behaviour (UI spec §5, §6.4):
- **Grid:** virtual scrolling; numbers right-aligned; `NULL` shown as a dimmed `null`; long text truncated with the full value on hover.
- **Status line:** row count, duration, a **read-only** badge, and **truncated** when set.
- **Sorting and filtering:**
  - When the result isn't truncated, sorting and filtering are client-side, **typed by `column_types`**: numeric strings compare as numbers, dates as dates.
  - When it is truncated, any sort or filter re-runs through `useWorksheetRun.run(sql, {sort, filters})`. A banner says "Truncated at 1,000 rows: sorting and filtering run on the server over all rows".
  - The backend's duplicate-columns `warning` shows as a banner.
- **Errors:** the guard's reason shown verbatim; a timeout with its hint; cancelled.
- **Preview 100 rows and Insert SELECT** (from Task 6's menu) now work: Preview runs `select * from marts.<t> limit 100` in a new worksheet.

- [ ] **Step 1: Vitest:**
  - `ResultGrid sorts numeric strings numerically`
  - `ResultGrid renders empty and null cells`
  - `truncated results sort on the server`
  - `duplicate-columns warning is shown`
  - `guard rejection shows the reason`
  - `Preview opens a worksheet and runs`

  Run (FAIL) → implement → run (PASS). Commit `feat: Explorer result grid with client and server sorting and filtering`.

---

### Task 9: Docker image, compose `ui` service, end-to-end tests

**Model:** implementer `sonnet` · reviewer `sonnet`

**Adds tooling:**
- `ui/Dockerfile`:
  - **stage 1** (`node:22-alpine`): `npm ci && npm run build` in `ui/frontend`
  - **stage 2** (`python:3.12-slim` + uv), following `agent/mcp-server/Dockerfile`: `COPY pyproject.toml uv.lock`, `uv sync --frozen --no-default-groups --group ui --group mcp --no-install-project`, then `COPY ui/backend/src` and `agent/mcp-server/src`; `PYTHONPATH=/app/ui/backend/src:/app/agent/mcp-server/src`; the built `dist/` at `/app/static`
  - command `uvicorn sherpa_ui.app:container_app --host 0.0.0.0 --port 8090 --workers 1`; non-root user
- compose service **`ui`**, modelled on the `mcp` service (read its comments; the same least-privilege and stale-mount reasons apply):
  - **no `env_file`**: an explicit `environment:` with `SHERPA_DB_HOST: postgres`, `SHERPA_DB_PORT: "5432"`, `SHERPA_DB_NAME`, `SHERPA_UI_USER`, `SHERPA_UI_PASSWORD` (`:?set it in .env`) and `SHERPA_UI_DBT_TARGET_PATH: /app/dbt/target-prod`, so the web-facing container never sees the admin login or `POSTGRES_PASSWORD`
  - volume `./data-platform/dbt:/app/dbt:ro` (the dbt folder, not `target-prod` itself, for the reason in the `mcp` service's comment)
  - `mem_limit` and `restart: unless-stopped` as for `mcp`
  - ports `127.0.0.1:${SHERPA_UI_PORT:-8090}:8090`
  - `depends_on: postgres` healthy
  - a healthcheck that GETs `/api/health` and passes on any 200 (a missing dbt artifact is a banner in the UI, not an unhealthy container, so `make up` works before `make build-prod`)
- the `ui` service joins `make up`, which already starts every service with `--build` (agent-design-1); no per-service target. Makefile **`e2e`** (`cd ui/frontend && npx playwright test` with `SHERPA_UI_PORT` and `SHERPA_UI_USER` passed through, base URL `http://127.0.0.1:${SHERPA_UI_PORT:-8090}`), and **`e2e-freshness`**, the only target that runs the freshness spec (Step 3), which changes the shared warehouse
- Playwright config and its browser install documented in the guide. Playwright browsers are downloaded once; the tests themselves run offline.

**Interfaces:** `sherpa_ui.app:container_app` is the app with `static_dir=/app/static`.

- [ ] **Step 1: Playwright `e2e/explorer.spec.ts`** against the real stack (`make up && make build-prod`):
  - **tree and Inspector:** the tree lists the nine marts; clicking `fct_orders` shows its grain in the Inspector
  - **run:** `select count(*) from marts.fct_orders` shows the same count as an admin `psql` check done in the test's setup through `docker compose exec`
  - **guard:** `delete from marts.fct_orders` shows the guard reason
  - **truncation:** `select * from marts.fct_order_items` shows the truncated banner; sorting `order_item_id` desc shows the maximum id first (server mode)
  - **cancel:** a long aggregate is cancelled with the button
  - **Preview:** Preview 100 rows works
- [ ] **Step 2: Playwright `e2e/offline.spec.ts`:** route every request that isn't to `http://127.0.0.1:8090` to `abort()` and fail the test on any such request. Load `/explorer` and run a query. Expected: no blocked request, and the screen works (criterion 1).
- [ ] **Step 3: Criterion 2:** `make generate DAYS=1 && make build-prod`, then reload Explorer. The `fct_orders` count changes without restarting `ui` (`e2e/freshness.spec.ts` asserts the count changed; it skips itself unless `SHERPA_E2E_FRESHNESS` is set, which only `make e2e-freshness` does).
- [ ] **Step 4:** Commit `feat: ui image, compose service and Explorer end-to-end tests`.

---

### Task 10: Documentation and how-to guides

**Model:** implementer `sonnet` · reviewer `haiku` (prose against commands already run; the reviewer checks references, not design)

**Files:**
- Create `docs/guides/basic/using-the-explorer.md`, `docs/guides/advanced/extending-the-ui.md`.
- Modify `docs/guides/basic/building-the-warehouse.md` (roles, audit table), `README.md`, `CLAUDE.md`.

All docs describe the current state only. Run every command once while writing, and use real output and screenshots-in-words.

- [ ] **Step 1: `basic/using-the-explorer.md`:**
  - **starting it:** `make up && make build-prod` (the UI starts with everything else), or `make ui-dev` for development; opening `http://127.0.0.1:8090`
  - **using it:** the tree, search, hover and Inspector; worksheets and history; running, Run selection, Format, Cancel; reading the status line
  - **sorting and filtering,** and when they run on the server
  - **what you can't do,** and why: read-only, marts only, the row cap, the 30 s timeout, masked PII (masked by column name, as for the agent in agent spec §4.2: a PII column aliased to another name is not masked)
- [ ] **Step 2: `advanced/extending-the-ui.md`:**
  - the backend/frontend split; adding an endpoint (Pydantic model → router → `make ui-client` → hook)
  - adding a screen (route, nav entry, the "only existing screens" rule)
  - where the read-only rules live and why they're shared with the agent
  - running the three test layers
- [ ] **Step 3: `building-the-warehouse.md`:**
  - the three roles, and `make roles` for existing volumes
  - `grant-read`
  - `ops.dbt_run_results`: what is recorded and how to query the last full build
- [ ] **Step 4: `README.md`:**
  - status
  - Quickstart: the UI URL after `make up`
  - layout: `ui/`
  - Stack: the UI row marked as installed
  - Guides list
- [ ] **Step 5: `CLAUDE.md`:**
  - Commands: `roles`, `grant-read` (replacing `grant-agent`), `up` now also starting `ui`, `ui-dev`, `ui-client`, `e2e`, and what `build-prod` now also does
  - Tests: the frontend test commands and markers
  - Tooling: ESLint, `tsc`, the client check
- [ ] **Step 6:** Check every command, path, module, component and Make target named in the touched docs exists. Expected: none dangling.
- [ ] **Step 7:** Commit `docs: Explorer guides, README and CLAUDE.md for ui-1`.
