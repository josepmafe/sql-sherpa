# Extending the UI

How to change the web app: add an API endpoint, add a screen, change the read-only rules, and run the three layers of tests that prove a change. Each section lists the files to touch, in order. Read [Using the Explorer](../basic/using-the-explorer.md) first. Commands run from the repository root unless a `cd` says otherwise.

## The split

The backend is the Python package `sherpa_ui` in `ui/backend/src/sherpa_ui/`; the frontend is a React + TypeScript + Vite app in `ui/frontend/`. In production one process serves both: FastAPI answers `/api/...` and serves the built frontend for every other path (`container_app` in `app.py`, used by `ui/Dockerfile`). In development they run apart: `dev_app` is the API alone, and Vite proxies `/api` to it.

| Where | Holds |
|---|---|
| `ui/backend/src/sherpa_ui/app.py` | `create_app(settings, static_dir)`: builds the services once, mounts the routers under `/api`, serves the frontend. The only place the pieces are wired |
| `api/` | One router per resource (`health.py`, `catalog.py`, `queries.py`), the request/response models and the error shape (`errors.py`). Routers are thin: they call a service |
| `catalog/`, `health/` | `CatalogService` and `HealthService`, built on `sherpa_mcp`'s `MartCatalogLoader` (the dbt artifacts) |
| `readonly/` | `QueryRunner` (guard, role, timeout, cancel, error positions, the batched fetch), `bounded_result.py` (row cap, masking, cell cut and result budget) and `sort_filter.py` (the server-side wrapper). Pure logic and I/O are kept apart |
| `settings.py` | `UiSettings`, the only place that reads configuration (`SHERPA_UI_*`, `SHERPA_DB_*`) |
| `openapi_export.py` | Writes the OpenAPI schema the frontend's types are generated from |
| `ui/frontend/src/api/` | `client.ts` (typed `openapi-fetch` client), `queries.ts` (TanStack Query hooks), `schema.d.ts` (generated, committed) |
| `ui/frontend/src/app/` | `AppShell.tsx`, `routes.tsx`, `navItems.ts`, `HealthBanner.tsx` |
| `ui/frontend/src/screens/<screen>/` | One folder per screen (today `explorer/`) |
| `ui/frontend/src/components/` | Components shared between screens (`ResultGrid`, `SqlEditor`) |

Tests: `ui/backend/tests/` mirrors the package, `ui/frontend/tests/` holds the Vitest tests and `ui/frontend/e2e/` the Playwright specs.

## 1. Add an endpoint

Example: `GET /api/catalog/domains`, the domain names. Five steps, in this order, so each is proved before the next one builds on it.

1. **The model and the service method.** Responses are frozen Pydantic models; no bare dicts cross the boundary. Add the model and the method to the service that owns the data (`catalog/catalog_service.py` for the catalog). Test the method on its own in `ui/backend/tests/` with a catalog built from fixtures, with no HTTP.
2. **The route** in the resource's router (`api/catalog.py`): read the service from `request.app.state`, run blocking work with `run_in_threadpool` (the services call psycopg and read files), and return the model. Failures raise `ApiException(status, code, message)`; the body is always `{code, message, detail?}`. A new error code from the query runner also needs its HTTP status in `QUERY_ERROR_STATUS` (`api/errors.py`). Test it through `TestClient` in `ui/backend/tests/api/`, including the unhappy path (unknown name, missing artifacts).
3. **Regenerate the client types:**

   ```bash
   make ui-client
   ```

   It exports the OpenAPI schema (`uv run python -m sherpa_ui.openapi_export`) and runs `openapi-typescript`, rewriting `ui/frontend/src/api/schema.d.ts`. Commit the result. `make lint` regenerates the file into a temporary path and fails with a `diff` if the committed one is stale, so a forgotten step cannot slip through.
4. **The hook** in `ui/frontend/src/api/queries.ts`, next to `useCatalog`: a `useQuery` that calls `api.GET("/api/catalog/domains")`. The types come from `schema.d.ts`, so a wrong path or response shape is a `tsc` error. Mutations (like running a query) call `api.POST` from the component or a hook of the screen.
5. **Use it** in a component, and test the component with Vitest and mocked API responses (`ui/frontend/tests/`).

## 2. Add a screen

Example: Lineage in ui-2. The shell lists only screens that exist, so a half-built screen never appears in the navigation.

1. Create `ui/frontend/src/screens/<name>/<Name>Screen.tsx` with its components next to it. Anything a second screen would share goes to `src/components/` only when that second screen exists.
2. Add the route in `src/app/routes.tsx`, as a child of the `AppShell` route.
3. Add the entry to `NAV_ITEMS` in `src/app/navItems.ts` (`{ path: "/lineage", label: "Lineage" }`), in the order of the UI spec's navigation: Home, Ask, Explorer, Lineage. The `/` route redirects to the Explorer until Home arrives; Home's milestone changes that redirect.
4. Enable what the earlier screens left disabled: the Explorer's "Open in Lineage" menu item and Inspector button use `LINEAGE_DISABLED_HINT` from `screens/explorer/DisabledHint.tsx`. Wire them to the route and remove the hint.
5. Add a Playwright spec in `ui/frontend/e2e/` for the screen's main flow, and a case to `offline.spec.ts` so the new screen loads with every request outside the app's origin blocked (no CDN or font fetches).

State that belongs to the user lives in `localStorage` under a `sherpa.v1.<screen>` key, never result rows. A change to a stored shape bumps the version in the key or migrates it in the screen's store (`worksheetStore.ts` is the model: it resets with a console warning when the stored value has an unexpected shape).

## 3. Where the read-only rules live

Three layers, none of which trusts the one before it:

| Layer | Where | Does |
|---|---|---|
| The SQL guard | `sherpa_mcp.sql_guard.SqlGuard`, built in `QueryRunner` from `catalog.mart_columns()` | One `SELECT`/`WITH` statement, the function allow-list, only `marts.<visible mart>` tables and real columns, no `SET`/`RESET`/`ALTER`, no whole-row references |
| The connection | `UiSettings.ui_conninfo()` in `settings.py` | Every session starts with `default_transaction_read_only=on` and the statement timeout |
| The role | `data-platform/postgres/roles.sql` | `sherpa_ui` is `LOGIN`, read-only and time-limited by role defaults, has no `CREATE`/`TEMPORARY`, and has only the grants dbt's `grant_read_access` macro gives (`marts`, `extensions`, `ops.dbt_run_results`) |

The guard and the masking (`sherpa_mcp.masking.PiiMasker`) are imported from `sherpa_mcp`, the agent's tool server, not copied. The Explorer and the agent must agree on what a read-only query is and on what is masked, and one implementation guarantees it. A change to the guard is therefore a change to both: follow [Extending the MCP server](extending-the-mcp-server.md) (and its sqlglot rules), then rerun the UI's tests (below). The runner builds its `SqlGuard` with the default function allow-list, the agent's.

What belongs only to the UI is in `readonly/`:

- `query_runner.py`: the timeout from `SHERPA_UI_STATEMENT_TIMEOUT_S` (also checked between FETCHes), the batched fetch (`FETCH_BATCH_ROWS`, `FETCH_BATCH_CELLS`), cancel (a registry from `query_id` to connection), and the mapping of Postgres error positions back to the user's SQL.
- `bounded_result.py`: what a result may hold. `BoundedResult` converts and masks each batch, cuts every cell with `sherpa_mcp.database.cap_cell` (10,000 characters, the MCP server's cut), and stops at the 1,000-row cap (`SHERPA_UI_ROW_CAP`) or at `MAX_RESULT_CHARS` (2,000,000), marking the result `truncated`. It is pure, so its rules are tested in `tests/readonly/test_bounded_result.py` without a database. The cell cut is shared with the MCP server on purpose: the guard's `MAX_PAD_LENGTH` assumes it. The server-side sort/filter wrapper is built in `readonly/sort_filter.py` from validated result-column names and bound parameters; the user's SQL itself is still guard-checked as written.

To loosen or tighten a rule:

1. Write the failing test first, in `ui/backend/tests/readonly/test_query_runner.py` (pure rules) or `tests/api/test_queries_api.py` (through HTTP, `warehouse` marker for the ones that need real marts).
2. Change the code, then run the `warehouse` tests, which exercise the real role.
3. If the role's privileges change, edit `data-platform/postgres/roles.sql` or the `grant_read_access` macro, run `make roles` and `make grant-read`, and update the data platform spec's role table.

## 4. The three test layers

```bash
cd ui/frontend && npm ci                                  # once: the frontend's packages (Node 22+)

# 1. Backend: pytest, no browser
uv run --env-file .env pytest ui                         # units and API tests, no database needed
uv run --env-file .env pytest ui -m warehouse            # against the built prod marts as sherpa_ui (make build-prod first)

# 2. Frontend: Vitest + Testing Library, mocked API
cd ui/frontend && npm test
cd ui/frontend && npm run lint && npm run typecheck      # ESLint and tsc

# 3. End to end: Playwright against the compose stack
cd ui/frontend && npx playwright install chromium        # once, downloads the browser
make up && make build-prod
make e2e
```

- `make lint` covers ruff, mypy, sqlfluff, ESLint, `tsc` and the stale-client check in one go.
- `make e2e` runs `npx playwright test` in `ui/frontend`, with one worker. Its base URL is `http://127.0.0.1:${SHERPA_UI_PORT:-8090}`; with the stack on another port, run `SHERPA_UI_PORT=8091 make e2e`.
- The specs are `explorer.spec.ts` (tree and Inspector, a count that matches an admin `psql` count, a guard rejection with its reason, server-side sort on a truncated result, Cancel of a long aggregate, Preview 100 rows), `offline.spec.ts` (every request outside the app's origin blocked) and `freshness.spec.ts`.
- **`freshness.spec.ts` changes your warehouse.** To prove a rebuilt warehouse shows up on reload, it runs `make generate DAYS=1` and `make build-prod`, which adds one simulated day. It can do that only while the data ends before yesterday, so it runs at most once per day and skips itself otherwise ("the warehouse already runs through yesterday"). It cannot be undone except by `make reset` and a full rebuild. To run the other specs only: `cd ui/frontend && npx playwright test explorer offline`.

## Next

- [Extending the MCP server](extending-the-mcp-server.md): the guard and masking the UI shares.
- [Adding or changing a mart](adding-or-changing-a-mart.md): a new mart appears in the Explorer after `make build-prod`; no UI change is needed.
