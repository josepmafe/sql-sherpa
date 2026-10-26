# SQL Sherpa — UI Spec: Self-Service Analytics Frontend

**Status:** Approved · **Date:** 2026-10-24 · **Depends on:** Data Platform Spec (`2026-10-22-sql-sherpa-data-platform-design.md`, interface in its §11) and Agent Spec (`2026-10-22-sql-sherpa-agent-design.md`, the `plan`/`answer` contract in its §5)

---

## 1. Intent

SQL Sherpa is a self-service analytics product: a documented data platform plus an agent that turns business questions into verified SQL. This spec is its face: a web app, run locally with docker-compose, where people look at the state of the business, ask questions, explore the marts and see how the warehouse is built.

The output must be production-like, the same bar as the other specs: typed contracts end to end, explicit failure states, tested screens. The owner is not a frontend developer, so the frontend uses a mainstream, well-documented stack and stays conventional.

### Users
- **Business stakeholders**, who ask questions and read the answers and the headline numbers.
- **Data analysts**, who write and check SQL and reuse what the agent wrote.
- **Data modellers**, who change the dbt project and need to see what feeds what.

There are no accounts: everyone uses the same anonymous, read-only access (§5).

### Screens
| Screen | Job |
|---|---|
| **Home** | The state of the business (headline numbers + a trend) and the state of the data (as-of date, last build, freshness, failing tests), with an "Ask a question…" box on top |
| **Ask** | Ask a question; review and correct the agent's plan and SQL **before** it runs; then see the answer with table, chart, SQL and reasoning |
| **Explorer** | Browse the marts with their dbt documentation; write and run read-only SQL |
| **Lineage** | The dbt project as a raw → marts flowchart, for one table's path or a whole domain, with each node's SQL and last build status |

### Success criteria
1. **Self-contained in docker-compose.** Every screen runs from `docker compose up` with no internet: no CDNs, and fonts, icons and JS are all bundled. The only exception is Ask's model and tracing calls, which degrade to a clear "model unavailable" state while the other screens keep working.
2. **Always live.** No data is baked into the frontend build or cached long enough to go stale. After adding data in the backend (`make generate` + `make build-prod`), a browser refresh shows it on every screen.
3. **Every query really hits the warehouse.** Explorer and Ask results come from executing SQL against Postgres through the read-only path, never from mocks or stored results.
4. **Every visual is drawn from data.** Home and Lineage render from live warehouse and dbt-artifact data, never from static images or hand-maintained JSON.
5. **One command to try it (ui-5).** Someone with only Docker can clone the repository and get the whole product with data, without Python, uv or make on their machine. This doesn't change the host-based development workflow.

### Non-goals
- Login, accounts and per-user permissions (and therefore staging or raw access for analysts).
- Writing anything to the warehouse.
- A chat: Ask is one standalone question per run; refining a run re-plans the same question.
- Column-level lineage, pinned results on Home, explaining SQL in words, CSV export, column profiling, answer feedback (§13).

### Anti-goals
- A home-grown BI tool. Home has a fixed, small set of curated numbers.
- Business logic in the frontend. Rules live in the backend or in dbt; the frontend renders.
- Screens that look fine with mocks and break against the real stack.

---

## 2. Constraints & Tooling

| Concern | Decision |
|---|---|
| Frontend | **React + TypeScript + Vite** (latest stable, pinned in `package.json` + lockfile) |
| Components | **Mantine** (layout, navigation, forms, notifications, tables) and **Mantine Charts** (Recharts underneath) |
| Lineage graph | **React Flow** (`@xyflow/react`) with **ELK** (`elkjs`) for layered auto-layout |
| SQL editor | **CodeMirror 6** (`@uiw/react-codemirror` + `@codemirror/lang-sql`), autocomplete fed from the catalog endpoint |
| Result grid | **Mantine React Table** (TanStack Table underneath), with virtual scrolling, if its release supports the pinned Mantine major. Otherwise, TanStack Table with Mantine's `Table` and TanStack Virtual. Checked when pinning in ui-1 |
| Data fetching | **TanStack Query**: short `staleTime` (≤ 30 s) and refetch on window focus, so data is never stale for long (criterion 2); health is also polled every 30 s. Only a request that got no answer is retried, once: an API error (4xx, a 503 for missing artifacts) is shown, with its code and message, not repeated. Ask's streams bypass it (below) |
| API types | Types generated from the backend's OpenAPI schema with `openapi-typescript`, called through `openapi-fetch`. Both are **committed** under `ui/frontend/src/api/` (the Node build stage has no Python to produce the schema). `make ui-client` regenerates them, and a check in `make lint` fails when the committed files differ from a fresh generation. The SSE event models are part of the schema, so streamed events are typed too |
| Backend | **FastAPI** (Python 3.12, same root `pyproject.toml`, dependency group `ui`), Pydantic models for every request and response. **One uvicorn worker**, because Ask drafts live in process memory |
| Streaming | **Server-Sent Events over `POST`**, read with `fetch` and a stream reader (the browser's `EventSource` only does `GET`) |
| Tests | pytest (backend), **Vitest** + Testing Library (frontend components), **Playwright** (one end-to-end flow per screen against the running stack) |
| Packaging | One `ui` image: a Node build stage, then a Python stage where FastAPI serves `/api` and the built static files |
| Offline | No runtime network fetches from the browser except to the backend. System font stack, bundled icons (`@tabler/icons-react`) |

### Repository layout

The repository groups code by spec: `data-platform/` (generator, dbt, Postgres init), `agent/` (`mcp-server/`, `core/`, `evals/`) and `ui/`. The prod artifacts, for example, live in `data-platform/dbt/target-prod/`. This spec adds `ui/`, and each of its folders appears in the milestone that first needs it. The `ui` image is built with the **repository root as build context**, because it installs `sherpa_mcp` and `sherpa_agent` as well.

```
ui/
├── Dockerfile                    # node build stage → python runtime serving /api + static
├── backend/
│   ├── src/sherpa_ui/
│   │   ├── app.py                # FastAPI app: routers, static files, SPA fallback
│   │   ├── settings.py           # UiSettings (SHERPA_UI_*)
│   │   ├── readonly/             # QueryRunner: sherpa_ui role, sql_guard, cap, masking, sort/filter, cancel
│   │   ├── catalog/              # marts tree + dbt docs (Explorer, autocomplete)
│   │   ├── lineage/              # graph from manifest, selector parsing, run status
│   │   ├── home/                 # home_queries.yaml + HomeService + data status
│   │   ├── ask/                  # plan/answer endpoints, SSE progress, chart rules
│   │   └── api/                  # one router per screen + health
│   └── tests/
└── frontend/
    ├── package.json  package-lock.json  vite.config.ts  tsconfig.json
    ├── src/
    │   ├── app/                  # shell: left nav, routing, theme, health banner
    │   ├── api/                  # generated client + TanStack Query hooks
    │   ├── screens/{home,ask,explorer,lineage}/
    │   └── components/           # ResultGrid, SqlEditor, ChartSwitch, Inspector, StepTimeline, …
    └── tests/                    # Vitest; e2e/ for Playwright
```

---

## 3. Architecture & Deployment

```
browser ──HTTP/SSE──► ui (FastAPI + static) ──► Postgres marts   (role sherpa_ui, read-only)
                         │      │
                         │      └── dbt artifacts: target-prod/ mounted read-only
                         └── sherpa_agent (in-process) ──MCP over HTTP──► mcp ──► Postgres marts (sherpa_agent)
                                         └── Bedrock (internet) · Logfire (internet, optional)
```

- **Services:** `postgres` (data platform), `mcp` (agent spec, HTTP transport), **`ui`** on `127.0.0.1:${SHERPA_UI_PORT:-8090}`. Port 8090 avoids `dbt docs serve`'s default of 8080. `phoenix` is the agent spec's optional profile.
- **The agent runs inside the `ui` backend** as a library (`sherpa_agent`'s `plan`/`answer`, agent spec §5) and reaches the `mcp` service over Streamable HTTP at `http://mcp:8765/mcp`, the compose service name, not 127.0.0.1. This is the "several client processes" case that the agent spec's HTTP transport exists for.
- **Model credentials inside the container:**
  - The host's `~/.aws` is mounted **read-only** into `ui`, with `AWS_PROFILE` and `AWS_REGION` passed through from `.env`.
  - A corporate proxy's CA bundle, if `.env` sets `SSL_CERT_FILE` / `AWS_CA_BUNDLE`, is mounted read-only at the same path.
  - `LOGFIRE_TOKEN` comes from `.env`.
  - Nothing credential-related is baked into the image or committed. `make ui-dev` runs the backend on the host instead, using the host's credentials directly.
- **dbt artifacts:** `data-platform/dbt/target-prod/` is mounted read-only. The UI reads `manifest.json` and `catalog.json` from it (`make build-prod` regenerates `catalog.json` after every build), and `sources.json` for freshness (only `dbt source freshness` writes it).
  - **Build status comes from the database, not the files:** the `ops.dbt_run_results` audit table (data platform spec §8, "Added by ui-1"). The UI reads the latest invocation with `target_name = 'prod'`, `command = 'build'` and `is_full_run`. The table is needed because dbt rewrites `target-prod/run_results.json` on every compiling command, so the file can't say what the last full build did.
  - The backend reloads a file when its mtime changes, checked on each request that uses it. A failed reload of `manifest.json`/`catalog.json` (a file half-written by a running `make build-prod`) keeps the catalog that last loaded, with a warning in the log (`sherpa_mcp`'s `MartCatalogLoader`); only a backend that never loaded one answers `catalog_unavailable`.
  - A node with no row in that invocation shows "not in the last build".
- **Health:** `GET /api/health` reports each dependency separately:
  - the **database**, which Explorer, Home and history re-runs need
  - the **artifacts**, present and when they were generated, which Lineage and the catalog tree need
  - the **tool server** (`mcp`), which Ask needs
  - the **model**: `unknown` until the first Ask call, then `ok` or `unavailable` from the last call's result, kept for 60 seconds. There is no extra Bedrock call just to check it.

  The frontend shows a banner for each one that is down, disables only what depends on it, and leaves the rest working (criterion 1). "Tool server unavailable" and "model unavailable" are separate messages.
- **Model calls fail fast when offline:** the agent's Bedrock client uses a 5-second connect timeout, and connection errors are not retried, so a missing internet connection turns into "model unavailable" within seconds rather than after the full retry budget. Throttling and 5xx responses keep the agent spec's retries.
- **Bound to localhost** like Postgres: no auth, so the port must not be reachable from the LAN. A trusted-host check (`TrustedHostMiddleware`, allowing `localhost` and `127.0.0.1`) also rejects requests whose `Host` header is anything else, which stops DNS-rebinding pages from calling the API (and spending Bedrock credits).

---

### Demo mode (ui-5)

For people who want to *try* SQL Sherpa rather than develop on it:

```bash
cp .env.example .env
docker compose --profile demo up -d
# open http://127.0.0.1:8090 (the first start builds the warehouse, ~5 minutes)
```

- **The `demo` profile adds one one-shot service, `bootstrap`,** defined in `data-platform/bootstrap/`. Its image holds a **copy** of the code and the dbt packages, so dbt parses from the image, not from a slow host bind mount.
- **On start** it waits for `postgres` and checks, as `sherpa_admin`, whether `marts.fct_orders` exists and has rows.
  - **Yes:** it exits at once with "warehouse present". A warehouse you built yourself is never touched.
  - **No:** it runs the generator (`generate --days 365`, ending yesterday), then `dbt build` and `dbt docs generate` on the prod target with `--target-path target-prod`, then exits 0. On any failure it exits non-zero with the failing step's output.
- **It bind-mounts only `data-platform/dbt/target-prod/`** (read-write), the same folder the host's `make build-prod` writes and `mcp` and `ui` read. Both workflows share artifacts as they share the database.
- **Nothing depends on `bootstrap`.** `make up` (no profile) never starts it, so a development `make up` never kicks off a surprise build. In the demo, `mcp` and `ui` start immediately, the health banner reports the missing artifacts and the build in progress, and the screens fill in once the artifacts land (the backend reloads them on change, §3).
- **Ask in the demo** needs model credentials. Setting `ANTHROPIC_API_KEY` and `SHERPA_MODEL_DEFAULT=anthropic:<model>` in `.env` is enough (the agent spec supports the Anthropic API by model string), or a Bedrock profile in `~/.aws`. Without either, Ask shows "model unavailable", and Home, Explorer and Lineage work fully (criterion 1).
- **Ports** `5432`, `8765` and `8090` must be free on localhost; the README says which variable moves each.

## 4. Backend API

All responses are Pydantic models. All errors are `{code, message, detail?}` with a stable `code`. Clients branch on the `code`; the status only groups the codes: `422` for a refused request (`invalid_request`, `guard_rejected`, `sql_error`, `unknown_column`, `pii_column`), `404` for `table_not_found`, `409` for `duplicate_query_id` and `cancelled`, `429` for `busy`, `503` for `catalog_unavailable`, `database_unavailable` and `too_many_connections`, `504` for `timeout`, and `500` for `internal_error`. A timeout is `504`, not `408`: Chromium silently re-sends a POST answered `408` on a reused keep-alive connection, which would run the query twice. The OpenAPI schema declares every endpoint's error statuses with the `ApiError` model, and none with FastAPI's own validation-error shape, so the generated client types the errors the API really sends. The schema is served at `/openapi.json`; FastAPI's Swagger UI and ReDoc pages (`/docs`, `/redoc`) are off, because they load their scripts from a CDN (§1, criterion 1).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | §3 |
| `GET /api/catalog` | The Explorer tree: database → `marts` → domain → table → columns. Each table has name, description, grain, primary key and domain; each column has name, data type, description, `is_pii`, `is_primary_key`. From `manifest.json` (YAML) plus `catalog.json` (real types) |
| `GET /api/catalog/tables/{name}` | The Inspector: everything above plus `accepted_values`, `synonyms`, the metric doc blocks a column's description uses, and the table's tests |
| `POST /api/queries` | `{query_id, sql, sort?: [{column, direction}], filters?: [{column, operator, value}]}` → `{query_id, columns, rows, returned_rows, truncated, elapsed_ms, mode: "client"\|"server", warning?}`; §5. The **client generates `query_id`** (a UUID), so it can cancel while the request is still running. A duplicate `query_id` returns `409`. `sql` holds at most 100,000 characters and no NUL character (libpq would end the query text there, so Postgres would run less than the guard checked); either is `422 invalid_request` |
| `POST /api/queries/{query_id}/cancel` | Cancels a running query; idempotent, and a no-op for an unknown or finished id |
| `POST /api/queries/format` | `{sql}` → `{sql}` formatted with sqlglot (Postgres dialect); never executes |
| `GET /api/lineage?select=<selector>&layers=…&failing_only=<bool>` | Nodes and edges for the selection; §6.5 |
| `GET /api/lineage/nodes/{unique_id}` | The node panel: description, materialization, contract, domain, file path, raw and compiled SQL, columns, last full build's status and tests |
| `GET /api/home` | Tiles, trend and data status; §6.2 |
| `POST /api/ask/plan` (SSE) | `{question, corrections: [Correction]}` → progress events, then one plan outcome (`Draft`, `NeedsClarification`, `Unanswerable` or `Failed`); §6.3 |
| `POST /api/ask/answer` (SSE) | `{draft_id}` → progress events, then `Answered` or `Failed` |

Drafts are kept in the backend's memory, keyed by `draft_id`, for 1 hour (`SHERPA_UI_DRAFT_TTL_MINUTES`); an expired draft returns `code = "draft_expired"`, and the UI offers to re-plan. Nothing is persisted on the server.

**Disconnects:** Starlette doesn't stop an endpoint when the client goes away, so every long endpoint (`/api/queries`, both Ask streams) watches `request.is_disconnected()`. On disconnect, a query is cancelled and an Ask call is cancelled with it, so an abandoned tab stops spending Bedrock credits.

---

## 5. Read-Only Query Path

Explorer, Home and history re-runs all go through one `QueryRunner`; Ask's own execution goes through the agent's `run_sql` (agent spec §4). Both share the same guard.

- **Role `sherpa_ui`:** read-only, `SELECT` on `marts` and `USAGE` on `marts` and `extensions`, exactly like `sherpa_agent`, plus `USAGE` on `ops` and `SELECT` on `ops.dbt_run_results` for the build status. The build-status query is fixed backend SQL, not user SQL; user queries still go through the guard, which only allows `marts`. It has its own role so its timeout and audit trail are separate from the agent's. Its role defaults are `default_transaction_read_only = on` and `statement_timeout = 30s`, and it gets the same `PUBLIC` revokes.
  - **On a fresh volume,** the data platform's Postgres init script creates it.
  - **On an existing volume** (the init script only runs on an empty one, and `sherpa_admin` can't create roles), ui-1 makes the role SQL idempotent. It moves into `data-platform/postgres/roles.sql`, which the init script runs, and **`make roles`** runs it again as the `postgres` superuser inside the container, so no volume has to be wiped.
  - dbt's `grants` and `on-run-end` hook cover the role from ui-1 on (data platform spec §3). A build fails with Postgres's `role "sherpa_ui" does not exist` until `make roles` has run.
- **Connection options:** `default_transaction_read_only=on`, `statement_timeout=30s` (`SHERPA_UI_STATEMENT_TIMEOUT_S`, integer seconds, default 30, at least 1), set on the connection like the MCP server's.
- **Guard:** `sherpa_mcp.sql_guard` (agent spec §4.2), imported as a library: one statement, `SELECT`/`WITH` only, the function allow-list, every table `marts.<visible mart>`, every qualified column a real column, no `SET`/`RESET`/`ALTER`. The runner builds `SqlGuard(catalog.mart_columns(), allowed_functions=...)` with its own allow-list (the agent's is the default). A rejection returns `code = "guard_rejected"` with the guard's reason, which the UI shows verbatim.
- **Executor:** the runner's own (this section's timeout, row cap, result size, cancel and error positions); the MCP server's `AgentDatabase` is shaped for the model (5 s, 100 rows, a 50,000-character budget) and is not reused. The runner reuses only its value conversion, cell cut and data-error message (`json_safe`, `cap_cell`, `data_error_message`).
- **Row cap and result size:** 1,000 rows (`SHERPA_UI_ROW_CAP`), fetched from a server-side cursor in batches of 100 rows, fewer for a wide result (at most 10,000 cells a batch) and one at a time after a row over 1,000,000 characters.
  - **Cells:** a text, bytes (hex), array or JSON cell longer than 10,000 characters is cut and ends with ` …[cut at 10000 chars]`, as in the MCP server (10,000 is also the longest `lpad` the guard allows).
  - **Budget:** the whole result stops at 2,000,000 characters, after cutting and masking (about eight times the widest mart's 1,000 rows).
  - A 1,001st row, or a row that would pass the budget, means `truncated`: that row and the rest are left out.
  - Postgres times each FETCH on its own, so fetching also stops at the statement timeout, checked before each batch.
  - As for the agent (agent spec §4.2, known limits), a cell is cut only after Postgres built it and psycopg loaded it, so a huge `string_agg` still costs its memory while it is fetched; the `ui` container's 1 GB limit and restart policy bound it.
- **Concurrency:** `sherpa_ui` has `CONNECTION LIMIT 10`. At most 8 queries hold a connection at once (`MAX_CONCURRENT_QUERIES`), so the health check still gets one while the runner is full.
  - A 9th query returns `code = "busy"` (`429`) at once, without opening a connection: "too many queries are running (at most 8 at once); wait for one to finish or cancel one".
  - A run takes its slot just before connecting (after the guard and a last cancel check) and gives it back however it ends.
  - A connection that Postgres refuses for a connection limit (SQLSTATE 53300, for instance while another client of the same login holds connections) returns `too_many_connections` (`503`), not `database_unavailable`. libpq reports no SQLSTATE for a refused connection, so the runner recognises it by Postgres's messages for it.
- **PII masking:** the same name-based masking as the agent (agent spec §4.2). Neither `sherpa_ui` nor `sherpa_agent` has column-level `SELECT` grants, so the masking is by result-column name only: a PII column under an alias or inside an expression is not masked, and `pg_stats` shows the PII columns' stored values to a role that can read them.
- **Sort and filter:**
  - When the first run was **not** truncated, the frontend sorts and filters the rows it has (`mode = "client"`).
  - When it **was** truncated, the frontend re-sends the SQL with `sort`/`filters`, and the backend runs `select * from (<sql>\n) as q where … order by …` (`mode = "server"`). The newline before the closing parenthesis keeps a trailing `--` comment from swallowing it, and trailing `;` and whitespace are stripped first. The wrapper is built from validated column names (they must be result columns, and not masked ones) and bound parameters; the user's SQL is still guard-checked as written.
  - **Duplicate column names** in the result make server mode impossible (`q` would have ambiguous columns). The runner then runs the SQL as written within the same run (one connection, one `query_id`, so a cancel still reaches it) and answers `mode = "client"` with a `warning`. Sorting and filtering stay in the browser, with a banner saying they only apply to the rows shown and suggesting to alias the columns.
  - **Masked columns** are never sorted or filtered on the server: a sort or filter column that the masking hides (by result-column name, exactly like the masking, through `PiiMasker.masks`) returns `code = "pii_column"` (`422`). The real values would still decide the order and which rows match, so a `contains` filter on `email` would be an oracle for the addresses. The Explorer offers neither on a masked column in either mode (in the browser they would only see `***`): it takes the names from the catalog's `is_pii` columns and matches result columns like the masking, by name in any case.
  - Operators: `=`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `is null`, `is not null`.
- **Cancel:** the runner keeps `query_id → connection` for running queries. Cancel calls psycopg's `cancel_safe()` on that connection; a role may cancel its own queries, so no extra privilege is needed. Postgres ignores a cancel request that finds nothing running (between the probe and the wrapped query, or between two FETCHes), so the runner also checks for a cancel before each statement and each FETCH, and a cancelled run never sends another one. A query is also cancelled when the client disconnects (§4), or when the same worksheet starts a new run (the frontend sends cancel for the previous `query_id`).
- **Errors:** Postgres errors return `code = "sql_error"` with the message and, when Postgres gives one, the character `position`. Positions are counted in what was actually sent, so the backend subtracts the prefix it added (the cursor declaration and, in server mode, the wrapper) before returning. For "Run selection" the editor adds the selection's offset back before highlighting.
  - **Data exceptions** (SQLSTATE class 22: a failed cast, a division by zero, a value out of range) quote the offending value, which can be a masked one (`invalid input syntax for type integer: "<an email>"` for `select email::int …`). Their message is replaced by the exception's name and a remedy, the MCP server's own (`sherpa_mcp.database.data_error_message`, agent spec §4.2): `a value could not be converted or computed (InvalidTextRepresentation); check the literal format`. The position is kept.
- **Logging:** every run is logged with its `query_id`, outcome and duration, and the SQL only by its length and a short sha256, never its text (it can hold personal values). A failure that the warehouse or the server caused (`timeout`, `busy`, `catalog_unavailable`, `database_unavailable`, `too_many_connections`) is a warning with its message; any other is logged at info by its code alone, because the guard's and Postgres's messages can quote the query or a stored value.

---

## 6. Screens

### 6.1 Shell
- A left navigation rail: **Home**, **Ask**, **Explorer**, **Lineage**. The active screen is part of the URL (`/`, `/ask`, `/explorer`, `/lineage`), so reload and back/forward work.
- A health banner (§3). Light and dark theme following the OS.

### 6.2 Home
- **Ask box** at the top: typing and pressing Enter opens Ask with the question and starts planning.
- **Headline tiles** (5): each covers the last 30 days ending on `as_of_date`, with its change versus the 30 days before (▲/▼, green or red by whether up is good: refund rate up is red).

  | Tile | Definition |
  |---|---|
  | Net revenue | Σ `fct_orders.net_revenue` of non-cancelled orders by `order_date` (spec §6.6). Its tooltip says that recent orders' refunds are still arriving (up to about 40 days), so the latest period reads slightly high |
  | Orders | non-cancelled orders by `order_date` |
  | AOV | net revenue / orders (§6.6) |
  | Refund rate | Σ `fct_refunds.revenue_amount` **by `refund_date`** / Σ `gross_revenue` of non-cancelled orders by `order_date`, in the same window. It counts refunds by the date they were paid, so the latest period is not biased low by refunds that haven't happened yet |
  | New customers | customers whose first non-cancelled order (`fct_orders.is_first_order`) falls in the window, deleted customers included |
- **Trend:** weekly net revenue for the last 26 calendar weeks (Monday to Sunday, `dim_date.week_start`) up to the week containing `as_of_date`, which is marked as partial. Black Friday weeks are highlighted (`dim_date.is_black_friday_week`).
- **Data status strip:**
  - `as_of_date`
  - the last full prod build: time and node count, from `ops.dbt_run_results` (§3)
  - freshness warnings and errors, from `target-prod/sources.json`
  - failing tests: a count that links to Lineage with **"failing only"** on (§6.5)
- **Definitions:** tiles and trend are curated queries in `ui/backend/src/sherpa_ui/home/home_queries.yaml` (id, title, SQL, value format, whether up is good). They are SQL over `marts`, run through the read-only path on every request, and validated against the guard by a test. Metric definitions follow the data platform spec §6.6 (non-cancelled orders, refunds by order date).
- **When something is missing:** no marts yet shows an empty state pointing to `make backfill && make build-prod`; no artifacts hides the status strip's build items and says why.

### 6.3 Ask

Two steps per run: **plan** (no data query is executed, apart from the agent's value lookups) and **answer** (the query runs).

**Planning.** After the user submits a question, live progress lists the agent's steps as they happen (from `plan`'s progress events: e.g. "Read the warehouse scope", "Matched values", "Wrote and checked SQL"). The steps come from the run, never from a fixed list, because they differ per agent variant.

**The draft** (the plan outcome `Draft`):
1. **The SQL**, syntax-highlighted, with Copy and "Open in Explorer".
2. **"Based on" line:** metric(s), grounded values, time range, grain, and the number of assumptions.
3. **How it got there:** a timeline of the plan's steps. Each interpretation item sits under the step that produced it (agent spec §5.1) and is a **chip**:
   - blue for facts, such as a grounded value
   - amber for assumptions
   - orange for the user's corrections
4. **Corrections.** Clicking a chip opens its alternatives, which come from the item's `alternatives` (agent spec §5.1: each has a value, a label and an optional score):
   - **value match:** the other candidates the step found, with their similarity, or "other…" for free text
   - **metric:** the metric doc blocks
   - **time range:** two date pickers
   - **grain:** day / week / month / quarter / year
   - **assumption:** remove it (`action = remove`) or reword it

   A free-text box takes anything else. **"Re-plan with corrections"** calls `plan` again with the same question and **the full, current list of corrections**: the ones from earlier runs of this question that the user kept, plus the new ones. Each correction is self-contained, carrying the original item it overrides, so it doesn't depend on the previous draft. The result shows as a new draft, the next run of that question.
5. **"Show results"** (primary) calls `answer` with the draft.

**The answer** (the answer outcome `Answered`), top to bottom:
1. **The answer** in plain language, with the "Based on" line under it.
2. **Result table and chart side by side**, with caveats (truncated, partial period, empty result) right under the table.
3. **The SQL that ran.** If the agent revised it while answering (agent spec §5.4), a notice says so and why, with a diff against the draft's SQL.
4. **How it got there**, the draft's timeline plus the answer's steps.
5. **Footer:** latency, tokens and cost (plan + answer), variant, and both trace links (plan, answer).

**Chart rules** (deterministic, in the backend, unit-tested). The backend returns a chart spec `{type, x, y: [columns], series: column | None}` alongside the result; the frontend only draws it. "Temporal" means a `date` or `timestamptz` column; integer columns such as a year are categories.

| Result shape | Default |
|---|---|
| 1 row × 1 numeric column | number tile, no chart |
| 1 temporal column + ≥ 1 numeric column | line chart, one line per numeric column |
| 1 temporal column + 1 text column with ≤ 8 distinct values + 1 numeric column | line chart, one line per text value (`series`) |
| 1 text or integer column + 1 numeric column, ≤ 25 rows | bar chart, sorted by value |
| 1 text or integer column + 1 text column with ≤ 8 distinct values + 1 numeric column, ≤ 25 categories | grouped bar chart (`series`) |
| anything else | table only |

A **Bar / Line / Table only** switch overrides the default. It is greyed out where the shape can't be drawn: bars need a category, lines need a temporal x.

**Other outcomes:**
- **Needs clarification:** the candidate readings as buttons. Choosing one re-plans with it as a correction.
- **Unanswerable:** the reason and the closest thing the warehouse covers.
- **Failed:** the reason; the last SQL and error if any.
- **Model unavailable:** the model call failed to connect, or health reports the model `unavailable` (§3). Ask is disabled with an explanation and a retry button, and the other screens keep working.
- **Tool server unavailable:** health reports `mcp` down. Same treatment, with its own message.

**Trace link:** built from `SHERPA_UI_TRACE_URL_TEMPLATE` (with `{trace_id}`), set for Logfire cloud or local Phoenix. Without it, the footer shows the trace id with a copy button.

**History** (browser storage, §7): questions, each with its runs. A run stores its corrections, the **whole draft** (interpretation, steps, SQL), the **final SQL** (the draft's, or the revised SQL when `sql_revision` is set), the outcome type and the time. Result rows and answer text are not stored.

Opening a past run shows its stored draft and **re-executes its final SQL through the read-only path** for live numbers (criterion 3). The answer text isn't kept, because its numbers may have changed. "Ask again" runs `plan` fresh with the run's corrections. A stored draft can be viewed, but not answered: its `draft_id` has expired on the backend, so "Show results" on it means re-planning.

### 6.4 Explorer

**Catalog tree (left):**
- `sherpa › marts › <domain> › table › column`, only what the read-only path can query, with search over table and column names.
- Columns show their type plus PK and PII badges.
- **Hover:** a tooltip with the description, grain and PK.
- **Click:** the **Inspector** (right panel) with everything from `GET /api/catalog/tables/{name}`, plus "Open in Lineage".
- **Table menu** (right-click and a "⋯" button): Preview 100 rows (`select * from marts.<t> limit 100`), Insert SELECT into the editor, Copy name, Open in Lineage.

**Editor (centre top):**
- **Worksheet tabs** and per-browser **history**.
- Schema-aware autocomplete.
- **Run** (Ctrl+Enter), **Run selection**, **Format** (`POST /api/queries/format`, sqlglot, Postgres dialect).
- While a query runs, Run becomes **Cancel**.

**Results (centre bottom):**
- The grid, with row count, duration, a read-only badge and a truncated flag.
- Column sort and filter, client-side or server-side as in §5, with a banner naming the mode when the result is truncated.
- Errors: guard rejections are explained, and Postgres errors highlight their position in the editor. A cancelled query shows "Cancelled after N s".

### 6.5 Lineage
- **Modes:** **Whole project**, **Table path** (one node, with upstream and downstream depth controls: `all` or 1–5), **Domain** (every mart of a `meta.domain` plus all their ancestors). A layer filter (sources, seeds, staging, intermediate, snapshots, marts, exposures) hides layers, and a **"Failing only"** toggle keeps just the nodes whose last full build failed them or one of their tests, plus the edges between them.
- **Selector box:** every mode is shown as **real dbt selector syntax**, and typing one applies it. "Copy dbt selector" therefore gives a string `dbt ls --select` accepts. Domain mode reads `+config.meta.domain:sales`. The backend implements this subset on the manifest graph, without running dbt:
  - **atoms:** a node name, `source:<source>` or `source:<source>.<table>`, `exposure:<name>`, `config.meta.domain:<d>`
  - **graph operators on any atom:** `+atom`, `atom+`, `n+atom+m`
  - **unions:** atoms separated by spaces

  Anything else returns `code = "unsupported_selector"`, listing what is supported. An integration test checks that, for a fixed list of selectors, the backend's node set equals `dbt ls --select <selector> --output json` on the prod manifest.
- **Layout:** left to right by layer, auto-placed with ELK, with edges from each node's `depends_on`.
- **Node styling:** colour by layer, a badge for materialization, red when the last full build failed it or one of its tests, and grey with "not in the last build" when the build didn't include it (§3).
- **Node panel:**
  - description, materialization, contract, domain and file path
  - **source and compiled SQL**
  - columns
  - the last full build: status, duration, tests
  - "Open in Explorer" (marts only), "Copy dbt selector"
- Names and SQL only, never data, so the whole project is visible to everyone.

---

## 7. Browser Storage

No accounts, so state that belongs to "the user" lives in the browser's `localStorage`, versioned (`sherpa.v1.*`) so a schema change can migrate or reset it:
- Explorer: worksheet tabs (name, SQL), query history (last 200: SQL, time, row count, status). Two browser tabs merge on the `storage` event instead of overwriting each other: a tab takes in the worksheets it lacks (by id; where both have one, its own copy wins) and the history entries it lacks (by time and SQL, last 200). Closing a worksheet does not propagate.
- Ask: questions and their runs (last 100 questions).

Browser storage never holds result rows; results are always re-fetched (criteria 2 and 3). It does hold SQL, interpretations and row counts, which can contain values from the warehouse such as a grounded product name in a `where` clause.

---

## 8. Testing

| Layer | How |
|---|---|
| Backend units | pytest: chart rules, selector parsing, sort/filter wrapper building, error mapping, health aggregation |
| Backend integration | pytest (marker `warehouse`): every endpoint against the built prod marts as `sherpa_ui`; guard rejections; truncation and server-mode sort/filter, including a trailing `--` comment and duplicate column names; error positions mapped back to the user's SQL; cancel of a long query (an **aggregate** over a cross join of `fct_order_items`, so the cursor can't return early); `home_queries.yaml` all pass the guard and return rows; the selector subset matches `dbt ls` |
| Ask backend | pytest with the agent's `TestModel`/`FunctionModel` fakes (agent spec §9): plan → draft → corrections → re-plan → answer, revised-SQL notice, every outcome type, SSE event order |
| Frontend components | Vitest + Testing Library with mocked API responses: chips and corrections, chart switch rules, grid modes, Inspector, error states |
| End-to-end | Playwright against `docker compose up` with a built warehouse: Home renders numbers matching a direct SQL query; Explorer runs, sorts (both modes), cancels and previews; Lineage table-path and domain modes and the node panel; Ask's flow is marker `live` (real Bedrock, on demand, never in CI) |
| Offline | Two checks. (1) Playwright blocks every browser request that isn't to the app's own origin, and every screen still loads, which proves there are no CDN or font fetches. (2) A compose override (`docker-compose.offline.yml`) puts `ui` and `mcp` on an **internal network with no internet**; Ask then shows "model unavailable" within seconds, and Home, Explorer and Lineage work fully. Stopping `mcp` separately shows "tool server unavailable" |

---

## 9. Configuration (`.env` additions)

| Variable | Default | Purpose |
|---|---|---|
| `SHERPA_UI_PORT` | `8090` | Port on 127.0.0.1 |
| `SHERPA_UI_USER`, `SHERPA_UI_PASSWORD` | `sherpa_ui`, — | The UI's read-only role |
| `SHERPA_UI_STATEMENT_TIMEOUT_S` | `30` | Per-query timeout, in whole seconds (at least 1) |
| `SHERPA_UI_ROW_CAP` | `1000` | Rows returned per query |
| `SHERPA_UI_DRAFT_TTL_MINUTES` | `60` | How long Ask drafts are kept |
| `SHERPA_UI_TRACE_URL_TEMPLATE` | — | e.g. a Logfire or Phoenix URL with `{trace_id}` |
| `SHERPA_UI_DBT_TARGET_PATH` | `/app/dbt/target-prod` (in the container) | Mounted artifacts |
| `MCP_HTTP_URL` | `http://mcp:8765/mcp` (in the container) | The agent's tool server |

The container also receives `AWS_PROFILE`, `AWS_REGION`, `LOGFIRE_TOKEN`, `ANTHROPIC_API_KEY` and `SHERPA_MODEL_DEFAULT` (for the demo's Anthropic-API option, §3) and, when set, `SSL_CERT_FILE` / `AWS_CA_BUNDLE` (§3).

---

## 10. Milestones

Each milestone has one plan, `docs/superpowers/plans/<date>-ui-<n>-<name>.md`, and ends with tests green, lint clean (including `eslint`, `tsc --noEmit` and the OpenAPI client check), and its acceptance met. **Every plan's last task is a documentation task** (`CLAUDE.md`, `README.md`, and how-to guides in `docs/guides/basic/` and `docs/guides/advanced/`), as in the other specs.

| Milestone | Builds | Needs | Acceptance |
|---|---|---|---|
| **ui-1 — Shell and Explorer** | `sherpa_ui` backend skeleton with health; the `sherpa_ui` role with idempotent `data-platform/postgres/roles.sql`, `make roles` and its dbt grants; the `ops.dbt_run_results` audit hook and `make build-prod` regenerating `catalog.json` (data platform spec §8, "Added by ui-1"); `QueryRunner` (§5); catalog endpoints; the frontend shell; Explorer (§6.4); the `ui` image and compose service; Make targets `ui-dev` (backend with reload + Vite dev server), `ui-client` and `e2e` | data-platform-2, agent-design-1 (`sql_guard`) | Explorer e2e flows pass; guard rejection, truncation banner, both sort modes and cancel work against the real stack; the browser offline check passes for Explorer; `make roles` on an existing volume creates `sherpa_ui` without data loss |
| **ui-2 — Lineage** | Lineage endpoints and selector subset, the graph, node panel, run-status overlay, "Failing only" | ui-1 | Table-path and domain e2e flows; the selector subset matches `dbt ls` and rejects an unsupported form; a failing test in the prod build shows its node red and appears under "Failing only" |
| **ui-3 — Home** | `home_queries.yaml`, `HomeService`, the data-status strip, Home screen | ui-2 (the failing-tests link) | Every tile equals a direct SQL check in the e2e test; after `make generate DAYS=1 && make build-prod`, a refresh changes `as_of_date` and the tiles (criterion 2); the build time and node count in the data-status strip match the latest full prod build in `ops.dbt_run_results` |
| **ui-4 — Ask** | Ask endpoints with SSE, chart rules, draft/corrections/answer screens, history with re-runs, trace links, model-health tracking and the offline compose override | ui-1, agent-design-3 | The Ask backend tests pass with agent fakes; the `live` e2e flow (question → draft → one correction → re-plan → Show results) passes once against Bedrock; the offline compose check shows "model unavailable" within seconds while the other screens work |
| **ui-5 — One-command demo** | `data-platform/bootstrap/` (Dockerfile + entrypoint), the compose `demo` profile, the health banner's "warehouse being built" state, the README "Try it" section | ui-4 | On a clean clone with only Docker: `cp .env.example .env && docker compose --profile demo up -d` brings up a working product (Home shows numbers, Explorer runs a query, Lineage shows the graph, Ask shows "model unavailable" without credentials); a second `up` finishes `bootstrap` in seconds; with a host-built warehouse, `bootstrap` changes nothing (`ops.dbt_run_results` gains no rows) |

---

## 11. Dependencies on the Other Specs

- **Data platform spec:**
  - the `marts` schema, `extensions` and `pg_trgm`
  - `meta.{domain, grain, primary_key, pii, synonyms}`, `metric_<name>` doc blocks, `accepted_values` tests, `as_of_date` and `fct_orders.is_first_order` (§6.5, §11)
  - the prod artifacts in `target-prod/` (`manifest.json`, `catalog.json`, `sources.json`) and the `ops.dbt_run_results` audit table
  - the `sherpa_ui` role, created by its idempotent roles script and granted like `sherpa_agent`
  - for ui-5's `bootstrap`: the generator CLI and the dbt project, run as `make backfill` and `make build-prod` would run them
- **Agent spec:**
  - `sherpa_mcp.sql_guard` and masking as a library (§4.2)
  - the `mcp` service over HTTP (§3)
  - `sherpa_agent`'s `plan(question, corrections)` → `Draft`, and `answer(draft)` → `Outcome` with progress events, step-attributed interpretation, and the revised-SQL record (§5)

---

## 12. Decisions Made by the Spec Author

- One `ui` container: FastAPI serves the API and the built SPA. No separate web server. Every path outside `/api` and `/assets` answers `index.html` with `Cache-Control: no-cache` (it names the build's hashed assets, so a reload after a new image gets the new ones); a missing file under `/assets` is a 404, not the page.
- The agent runs in-process in the UI backend and reaches `mcp` over HTTP.
- A separate read-only role `sherpa_ui` with a 30 s timeout and a 1,000-row cap; the agent keeps its 5 s and 100 rows.
- Sorting and filtering run in the browser when the result is complete, and as a server-side wrapper when it was truncated.
- Ask plans first and runs only on "Show results". Corrections re-plan the same question; there is no chat.
- Charts are picked by fixed rules with a user switch; no model chooses them.
- Home's numbers are a curated YAML of SQL queries, not a BI tool.
- Lineage is drawn from the manifest, not the embedded dbt docs site. The selector syntax is a supported subset, evaluated without running dbt.
- Per-user state lives in `localStorage`; drafts live in backend memory for an hour; nothing is persisted on the server.
- The trace link is a configurable URL template, because where traces live depends on the tracing setup.
- The client generates `query_id`, so it can cancel while a query is still running; disconnects cancel queries and Ask calls.
- Build status comes from the `ops.dbt_run_results` audit table, because dbt rewrites `run_results.json` on every compiling command.
- Model availability is learned from real calls (no probe), with fast failure on connection errors; tool-server and model outages are separate states.
- Lineage shows and accepts real dbt selector syntax, a tested subset of it.
- Refund rate on Home counts refunds by refund date, so the latest period isn't biased by refunds that haven't happened yet.
- `~/.aws` is mounted read-only into `ui` for Bedrock; nothing credential-related is in the image or the repo.
- Trying and developing are separate paths that share only the database and the artifacts folder: visitors use the `demo` profile and its one-shot `bootstrap`, while development stays host-based (fast, no bind-mounted dbt). `bootstrap` never rebuilds an existing warehouse, and nothing waits on it.

---

## 13. Later Ideas (explicitly out of scope)

- Login and roles, and with them staging and raw access for analysts.
- Pinning Ask answers and Explorer queries to Home.
- Home tiles from dbt saved queries, once data-platform-5 exists (the data platform spec adds saved queries together with this).
- Column-level lineage (would need SQL parsing of compiled models).
- Explaining SQL in plain language (a new agent capability with its own evals).
- CSV export (a row-cap policy question), column profiling, and 👍/👎 feedback on answers as eval-case candidates.
- A containerised development environment (a "toolbox" image with every dependency group, the Makefile delegating to it). It would make the repo developable with only Docker, at the cost of slow bind-mounted dbt on Windows and awkward pre-commit hooks.
- **Bring your own dbt project** (Postgres-based ones only): a guide and a small milestone after ui-5. The agent's project-specific constants become settings: the consumption schema (`MARTS_SCHEMA`), the `as_of_date` and first-order queries, the `does_not_cover` scope, the examples file, the tool-description wording. Home's `home_queries.yaml` becomes a setting too. PII masking also needs a name-pattern fallback, because a project without `meta.pii` tags would otherwise send personal data to the model unmasked. Other warehouses are out of scope (dialect, drivers, no `pg_trgm`).
