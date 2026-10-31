# Extending the UI

How to change the web app: add an endpoint, add a screen, change the read-only rules, and run the three test layers that prove a change. Read [Using the Explorer](../basic/using-the-explorer.md) first. Rules and design: the [UI spec](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md). Commands run from the repository root.

## The split

The backend is the Python package `sherpa_ui` (`ui/backend/src/sherpa_ui/`). The frontend is React + TypeScript + Vite (`ui/frontend/`). In production one process serves both: FastAPI answers `/api/...` and serves the built frontend for every other path (`container_app`). In development `dev_app` serves the API alone and Vite proxies `/api` to it (`make ui-dev`).

| Where | Holds |
|---|---|
| `app.py` | `create_app(settings, static_dir)`: builds the services once, mounts the routers under `/api`. The only place the pieces are wired |
| `api/` | One router per resource (`health.py`, `catalog.py`, `queries.py`, `lineage.py`), `errors.py` (error shape) and `body_limit.py` (1 MiB POST cap) |
| `catalog/`, `health/` | `CatalogService`, `HealthService`, built on `sherpa_mcp`'s `MartCatalogLoader` |
| `lineage/`, `build/` | `ManifestGraph`, `SelectorEvaluator`, `StatusDeriver` and `ColumnLineageBuilder` (pure); `ManifestLoader`, `NodeSql`, `ColumnLineageLoader`, `LineageService` and `BuildStatusReader` (file and database reads) |
| `readonly/` | `query_runner.py`, `bounded_result.py`, `sort_filter.py` |
| `settings.py` | `UiSettings`, the only place that reads configuration (`SHERPA_UI_*`, `SHERPA_DB_*`) |
| `openapi_export.py` | Writes the OpenAPI schema the frontend types come from |
| `ui/frontend/src/api/` | `client.ts` (typed `openapi-fetch`), `queries.ts` (TanStack Query hooks), `schema.d.ts` (generated, committed) |
| `ui/frontend/src/app/` | `AppShell.tsx`, `routes.tsx`, `navItems.ts`, `HealthBanner.tsx` |
| `ui/frontend/src/screens/<screen>/` | One folder per screen (`explorer/`, `lineage/`) |
| `ui/frontend/src/components/` | Components shared between screens (`ResultGrid`, `SqlEditor`) |

Tests: `ui/backend/tests/` mirrors the package, `ui/frontend/tests/` holds Vitest, `ui/frontend/e2e/` holds Playwright, `ui/parity/` holds the sort/filter case file that pytest and Vitest both run.

## 1. Add an endpoint

Example: `GET /api/catalog/domains`. Do the steps in order, so each is proved before the next builds on it.

1. **Model and service method.** Responses are frozen Pydantic models; no bare dicts. Add the method to the service that owns the data (`catalog/catalog_service.py`). Test it alone in `ui/backend/tests/`, with no HTTP.
2. **Route** in the resource's router (`api/catalog.py`).
   - Read the service from `request.app.state`; run blocking work with `run_in_threadpool`.
   - Raise `ApiException(status, code, message)`; the body is always `{code, message, detail?}`.
   - A new query-runner error code needs its status in `QUERY_ERROR_STATUS` (`api/errors.py`) and a title in `ERROR_TITLES` (`screens/explorer/WorksheetPanel.tsx`).
   - Declare every error status with `error_responses(*statuses)`. Every `/api` route gets `500`; the catalog and lineage routers add `503`. A route with a path or body parameter must declare `422`.
   - Add the route to the table in `TestOpenapiErrors` (`tests/test_app.py`).
   - Test through `TestClient` in `ui/backend/tests/api/`, including the unhappy path.
3. **Regenerate the client types** and commit the result:

   ```bash
   make ui-client
   ```

   `make lint` regenerates the file elsewhere and fails on a `diff`, so a stale client cannot slip through.
4. **Hook** in `ui/frontend/src/api/queries.ts`, next to `useCatalog`: a `useQuery` calling `api.GET("/api/catalog/domains")`. A wrong path or shape is a `tsc` error.
5. **Use it** in a component; test with Vitest and mocked API responses (`ui/frontend/tests/`).

## 2. Add a screen

The shell lists only screens that exist, so a half-built screen never appears in the navigation. Example: Lineage (`screens/lineage/`).

1. Create `ui/frontend/src/screens/<name>/<Name>Screen.tsx`. Move code to `src/components/` only when a second screen needs it.
2. Add the route in `src/app/routes.tsx`, as a child of the `AppShell` route.
3. Add the entry to `NAV_ITEMS` in `src/app/navItems.ts`, in the UI spec's order: Home, Ask, Explorer, Lineage.
4. Link the screens to each other. The Explorer's "Open in Lineage" (`CatalogTree.tsx`, `Inspector.tsx`) builds its URL with `lineageLink` (`screens/explorer/lineage.ts`); Lineage's "Open in Explorer" links to `/explorer?table=<name>`.
5. Add a Playwright spec in `ui/frontend/e2e/` for the main flow, and a case in `offline.spec.ts` (every request outside the app's origin is blocked).

Conventions the Explorer follows, which a new screen should too:

- **Mounted panels.** Worksheet panels stay mounted (`keepMountedMode="display-none"` on Mantine `Tabs`), so a tab switch never cancels a query. `WorksheetPanel` and `ResultGrid` are memoized: pass stable props (callbacks take the worksheet id and are wrapped in `useCallback` in `ExplorerScreen.tsx`). A fresh function or object per render fails the test `typing in one worksheet re-renders neither the other worksheets nor any result grid`.
- **Health.** Disable only what needs a down dependency, and say why (UI spec §3). The Explorer reads `useHealth()` once and passes a `blockedReason` string down.
- **Load errors.** A failed `useQuery` shows `LoadError` (`screens/explorer/LoadError.tsx`) with a Retry.
- **Width.** Three columns from 1280 px (`WIDE_QUERY`), a `Drawer` below. Run `e2e/layout.spec.ts` after changing the columns.
- **Browser storage.** User state lives in `localStorage` under `sherpa.v1.<screen>`, never result rows. Bump the version or migrate when a stored shape changes (`worksheetStore.ts`).

## 3. Extend Lineage

The selector and the statuses are pure code with an oracle, so a change starts from a test.

**Add a selector atom** (say `tag:<t>`):

1. Add cases to `ui/backend/tests/lineage/test_selector.py`: a match, no match, combined with `+`, and a malformed form.
2. In `lineage/selector.py`: a regex next to `_SOURCE_ATOM`, a branch in `SelectorEvaluator._match_atom` (before the bare-name branch), and the form in `SUPPORTED_FORMS`, the text of the `unsupported_selector` message. An atom that needs a new node field adds it to `GraphNode` in `lineage/manifest_graph.py`, with a test in `test_manifest_graph.py`.
3. Add selectors that use it to `SELECTORS` in `test_selector_oracle.py`. That test (marker `warehouse`, about 30 s: one in-process `dbt parse`, then `dbt ls` per selector) compares the evaluator with `dbt ls --select <selector>`, so it proves the atom means what dbt means:

   ```bash
   uv run --env-file .env pytest ui/backend/tests/lineage/test_selector_oracle.py -m warehouse
   ```

4. If the three modes should write it, change `ui/frontend/src/screens/lineage/selectors.ts` and its Vitest file; any other selector stays "custom". Then list it in the UI spec §6.5 and in [Using Lineage](../basic/using-lineage.md).

**Add a node status:**

1. Add a case to `NodeStatus` in `lineage/node_status.py` and to `StatusDeriver.node_status`, with tests in `tests/lineage/test_node_status.py` that include its precedence against `failed` and `warned`. A new dbt result status goes in `_NODE_STATUSES` or `_TEST_OUTCOMES`; an unknown one is logged and treated as no row.
2. The status travels in `LineageNode` (`lineage/lineage_service.py`): run `make ui-client` and commit the schema.
3. Give it a label, an icon and a colour in `STATUS` (`screens/lineage/GraphNode.tsx`); status is never colour alone, and the colours must pass AA contrast. Add a Vitest case.
4. Add the row to the status list in the UI spec §6.5 and to the Lineage guide.

**Add or change a column kind rule** (say a new kind, or what makes a column `transformed`). Column lineage is derived with sqlglot from the compiled SQL (UI spec §6.5), so a change starts from a test on a small SQL string:

1. Add a case to `ui/backend/tests/lineage/test_column_lineage.py`: a few lines of compiled SQL, the expected kind, resolution, notes and edges. Include the unhappy path (SQL that does not parse, an unknown relation, a column missing from the output).
2. Change `ColumnLineageBuilder` in `lineage/column_lineage.py` (pure: `build(compiled_sql)` takes strings). A new kind is a `ColumnKind` member, which reaches the OpenAPI schema. `ColumnLineageLoader` (`column_lineage_loader.py`) only reads the artifacts, caches per `manifest.json`/`catalog.json` change and builds under a lock; leave it alone unless the inputs change.
3. Add the new kind to `KIND` in `screens/lineage/ColumnLabels.tsx` (an icon and a label, never colour alone) and a Vitest case; run `make ui-client` first so `tsc` flags every place the kind is missing.
4. Add a trace to `TRACES` in `test_column_lineage_warehouse.py` when the rule changes which source columns a real mart column reaches. Read the expected set off `target-prod/compiled/` by hand, never from the builder's output. Run:

   ```bash
   uv run --env-file .env pytest ui/backend/tests/lineage -m warehouse
   ```

   `test_every_model_column_has_a_kind` also runs there, on the real project.
5. List the kind in the UI spec §6.5 and in [Using Lineage](../basic/using-lineage.md).

**A sqlglot bump** changes column lineage as well as the guard. Rerun the guard's hostile-input tests ([Extending the MCP server](extending-the-mcp-server.md)) and the whole lineage folder, including the warehouse traces above (the hand-checked sets must still hold). The column-focus end-to-end checks are in `e2e/lineage.spec.ts` (`make e2e`).

The shared warehouse should not hold a really failing build, so `lineage.spec.ts` skips its failing-node check by default. `cd ui/frontend && SHERPA_E2E_LINEAGE_FAILING=1 npx playwright test lineage` inserts a synthetic full build into `ops.dbt_run_results`, checks that the node turns red and that a warning stays out of "Failing only", and deletes the rows. A killed run leaves a fake newest build behind; the next run clears it first.

## 4. Where the read-only rules live

Three layers, none of which trusts the one before it:

| Layer | Where | Does |
|---|---|---|
| SQL guard | `sherpa_mcp.sql_guard.SqlGuard`, built in `QueryRunner` | One `SELECT`/`WITH`, function allow-list, only visible `marts` tables and real columns |
| Connection | `UiSettings.ui_conninfo()` | `default_transaction_read_only=on` and the statement timeout on every session |
| Role | `data-platform/postgres/roles.sql` | `sherpa_ui` is read-only, time-limited, with only the grants dbt's `grant_read_access` macro gives |

The guard and the masking (`sherpa_mcp.masking.PiiMasker`) are imported from `sherpa_mcp`, not copied. The Explorer and the agent must agree on what a read-only query is and what is masked, and one implementation guarantees it. A guard change is therefore a change to both: follow [Extending the MCP server](extending-the-mcp-server.md), then rerun the UI tests.

What belongs only to the UI is in `readonly/`:

- `query_runner.py`: timeout (`SHERPA_UI_STATEMENT_TIMEOUT_S`), batched fetch, cancel, the concurrency gate, and the mapping of Postgres errors to `UiQueryError`.
- The gate: `MAX_CONCURRENT_QUERIES` (8) stays below the role's `CONNECTION LIMIT 10`, with room for the health check. Another query fails `busy` without connecting.
- `bounded_result.py`: the 1,000-row cap (`SHERPA_UI_ROW_CAP`), the cell cut shared with the MCP server (the guard's `MAX_PAD_LENGTH` assumes it) and `MAX_RESULT_CHARS`. Pure, tested in `tests/readonly/test_bounded_result.py`.
- `sort_filter.py`: the server-side sort/filter wrapper. It refuses (`422 pii_column`) any column the masker hides, so the refusal and the masking agree. The grid hides those controls too (`maskedColumnNames` in `components/resultView.ts`).

To loosen or tighten a rule:

1. Write the failing test first: `tests/readonly/test_query_runner.py` (pure rules) or `tests/api/test_queries_api.py` (through HTTP; `warehouse` marker when it needs real marts).
2. Change the code, then run the `warehouse` tests, which exercise the real role.
3. If privileges change, edit `roles.sql` or the `grant_read_access` macro, run `make up` (re-applies the roles) and `make build-prod` (re-applies the grants), and update the data platform spec's role table.

## 5. The three test layers

```bash
(cd ui/frontend && npm ci)                                # once (Node 22+)

uv run --env-file .env pytest ui                          # 1. backend; the `integration` tests need Postgres (`docker compose up -d --wait postgres`)
uv run --env-file .env pytest ui -m warehouse             #    against the built prod marts (make build-prod first)

(cd ui/frontend && npm test)                              # 2. Vitest + Testing Library, mocked API
(cd ui/frontend && npm run lint && npm run typecheck)     #    ESLint and tsc

(cd ui/frontend && npx playwright install chromium)       # 3. once; then end to end on the compose stack
make up && make build-prod
make e2e
```

- Mock the API at `src/api/client` partially (`vi.mock(..., async (importOriginal) => ({ ...(await importOriginal()), api: { POST: ... } }))`), so `apiError` stays real. Give `beforeEach` a block body: Vitest treats a returned function as teardown.
- Vitest payloads come from the typed builders in `ui/frontend/tests/fixtures/api.ts` (`queryResponse`, `tableDetails`, `ok`, `apiFailure`, ...), which return the generated schema types, so a field the API adds breaks `npm run typecheck` in one place. Never pass a literal to a mocked `api`. `renderWithProviders` (`fixtures/render.tsx`) wraps a component in Mantine, a router and a QueryClient that never retries.
- E2E specs read their expected values from the warehouse at run time, not from constants.
- `make e2e` uses one worker and `http://127.0.0.1:${SHERPA_UI_PORT:-8090}`. For another port run `SHERPA_UI_PORT=8091 make e2e` or set it in `.env`; `make ui-dev` follows the same variable.
- Specs: `explorer.spec.ts`, `lineage.spec.ts`, `layout.spec.ts`, `offline.spec.ts`, `freshness.spec.ts`. Scrollbar overlap and layout widths show only in a real browser, hence Playwright.
- `make lint` covers ruff, mypy, sqlfluff, the docs coverage, ESLint, `tsc`, the npm lockfile and the stale-client check.

### The shared sort/filter case file

`ui/parity/sort_filter_cases.json` pins what a sort or filter means (UI spec §5). Two tests run it: `ui/backend/tests/readonly/test_sort_filter_parity.py` (real Postgres, through the server wrapper) and `ui/frontend/tests/resultViewParity.test.ts` (the browser's code, reading the file at test time).

- `tables`: `name`, `sql` (a `values` list), `columns`, `column_types` (grid types), `rows` (exactly what the API returns for `sql`, int8 above 2^53 as strings). Column 0 is the integer `id`; column 1 is the one sorted and filtered.
- `cases`: `name`, `table`, `sort` and `filters` (by column index), then `expected_ids`. Optional fields: `route: "server"` (the browser must send this one to the server), `expected_error` (Postgres refuses it; the browser must refuse too), `accepted_difference` with `client_expected_ids` (a documented place where the browser answers differently).
- Every table has at most 6 rows: Postgres sorts stably only below 7 rows, so a larger table with tied keys would make the expected order depend on its quicksort.

To add a case, or a new operator or column type (cases first):

1. Write the table and the case in the file. Take `expected_ids` from real Postgres through `build_wrapped_query` (`sherpa_ui.readonly.sort_filter`), never from the browser code.
2. Run both sides:

```bash
uv run --env-file .env pytest ui/backend -k parity
(cd ui/frontend && npm test)
```

3. A failing Vitest case is a browser difference: fix `components/resultView.ts`, or route that case to the server with `route`. A coverage test requires every operator and direction on every column type.

**`freshness.spec.ts` changes your warehouse.** It runs `make advance DAYS=1` and `make build-prod`, adding a simulated day to the warehouse every checkout shares; only `make reset` and a full rebuild undo it. `make e2e` skips it. Run it deliberately:

```bash
cd ui/frontend && SHERPA_E2E_FRESHNESS=1 npx playwright test freshness
```

It skips itself when the warehouse already runs through yesterday, so it runs at most once per day.

## Common errors

| Symptom | Fix |
|---|---|
| `make lint` fails on the `diff` of `schema.d.ts` | Run `make ui-client` and commit the file |
| `tsc` rejects an `api.GET` path | The client is stale: run `make ui-client` |
| OpenAPI shows `HTTPValidationError` for a route | Declare `422` with `error_responses(422)` |
| `TestOpenapiErrors` fails for a new route | Add the route and its statuses to its table |
| A new error shows "Query failed" | Add its code to `ERROR_TITLES` |
| The memoization test fails after adding a prop | Make the prop stable (`useCallback`, a string) |
| A parity case fails in Vitest only | The browser differs from Postgres: fix `resultView.ts` or route the case to the server; never edit `expected_ids` to match the browser |
| Playwright cannot connect | `make up && make build-prod`, and match `SHERPA_UI_PORT` |
| `unsupported_selector` for a form you added | `SelectorEvaluator._match_atom` has no branch for it |
| The oracle test fails for one selector | The atom differs from `dbt ls`: fix the evaluator, not the list |
| A new status draws nothing in the graph | Add it to `STATUS` in `GraphNode.tsx` |
| A new column kind renders blank, or `tsc` fails after `make ui-client` | Add it to `KIND` in `ColumnLabels.tsx` |
| A warehouse trace gains or loses a source column | The kind rule changed what the column reads: re-derive the set by hand from the compiled SQL |
| `freshness` is skipped | Expected without `SHERPA_E2E_FRESHNESS=1`, or when the data already ends yesterday |

## Next

- [Extending the MCP server](extending-the-mcp-server.md): the guard and masking the UI shares.
- [Adding or changing a mart](adding-or-changing-a-mart.md): a new mart appears in the Explorer after `make build-prod`; no UI change is needed.
