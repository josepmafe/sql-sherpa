# Using the Explorer

The Explorer is the web app's first screen: a tree of the marts with their dbt documentation on the left, a SQL editor with worksheets in the middle, and an Inspector for the selected table on the right. Everything it runs goes to the real prod warehouse through a read-only path. This guide shows how to start it, what each part does, how sorting and filtering behave, and what the Explorer will not let you do. It assumes the prod warehouse is built ([Building the warehouse](building-the-warehouse.md)).

## Starting it

The UI is a compose service, so it starts with everything else:

```bash
make up           # builds and starts postgres, mcp and ui; waits until all are healthy
make build-prod   # the prod warehouse and its dbt docs (catalog.json) that the UI reads
```

Then open <http://127.0.0.1:8090>. The root redirects to `/explorer`. `make up` works before `make build-prod`: the page loads with a red banner, "dbt artifacts missing: run `make build-prod` to see table descriptions and lineage", and the tree is empty until the build has run. A reload picks up a later build; the UI container does not need a restart.

The port comes from `SHERPA_UI_PORT` (default 8090, bound to 127.0.0.1). If something else already listens on 8090, start the stack with another one, for example `SHERPA_UI_PORT=8091 make up`, and open that port. Put the variable in `.env` to make it stick.

For development there is a second way (Node 22+, and `cd ui/frontend && npm ci` once): `make ui-dev` runs the API on the host with reload (port 8090) and the Vite dev server (<http://localhost:5173>), which forwards `/api` to it. Stop the compose `ui` service first (`docker compose stop ui`), because both use port 8090. See [Extending the UI](../advanced/extending-the-ui.md).

The left navigation lists only the screens that exist. Home, Ask and Lineage arrive in later milestones.

## The catalog tree

The tree is `sherpa > marts > <domain> > table > column`, built from the dbt artifacts: descriptions from `manifest.json`, real column types from `catalog.json`. It lists only what the read-only path can query, the nine marts.

- **Search** (top) filters tables and columns by name.
- **Columns** show their type, a PK badge for primary-key columns and a PII badge for the columns whose values are masked.
- **Hover** a table or column for a tooltip with its description.
- **Click** a table to open it in the Inspector on the right: description, domain, grain, primary key, synonyms, every column with its description, accepted values and the metrics it feeds, and the dbt tests on the table. The Inspector's "Open in Lineage" button is disabled, with a tooltip, until ui-2 adds Lineage.
- **Table menu** (right-click a table, or its "..." button):
  - **Preview 100 rows** opens a new worksheet with `select * from marts.<table> limit 100` and runs it.
  - **Insert SELECT** adds `select *`, `from marts.<table>` and `limit 100` (on separate lines) to the active worksheet, after whatever is already typed.
  - **Copy name** copies `marts.<table>` to the clipboard.
  - **Open in Lineage** is disabled until ui-2.

## Worksheets, history and running

Worksheets are tabs, each with its own SQL, its own editor undo history and its own running query. Add one with the plus button, rename it by double-clicking its tab, and close it with its x. Switching tabs never cancels a query that is running in another one.

The editor completes table and column names from the catalog. The buttons above it:

| Button | Does |
|---|---|
| **Run** (Ctrl+Enter, or Cmd+Enter) | Sends the whole worksheet. Disabled when it is empty |
| **Run selection** | Sends only the selected text. Disabled with nothing selected |
| **Format** | Reformats the SQL (sqlglot, Postgres dialect). SQL that does not parse is left alone and the reason is shown |
| **Cancel** | Replaces Run while a query runs; stops it in Postgres, and the result area shows "Cancelled after N s" |

While a query runs, "Running… N s" counts the seconds. Starting a new run in a worksheet cancels its previous one.

**History** (top right of the editor) lists the last 200 runs: SQL, time, row count and status. Picking one puts its SQL into the active worksheet. Worksheets and history live in the browser's `localStorage` under the key `sherpa.v1.explorer`, so they survive a reload but belong to one browser: another browser or a private window starts empty, and clearing site data removes them. A browser that blocks site data still runs the Explorer; its worksheets and history then last only until the page closes. Result rows are never stored; a re-run fetches them again.

### Reading the result

Above the grid, the status line reads `2 rows in 19 ms` and carries a `read-only` badge. If rows were left out, because the result hit the 1,000-row cap or its size budget (below), it also carries an orange `truncated` badge. The API's answer behind that line, for `select email from marts.dim_customers limit 2`:

```
{"columns":["email"],"column_types":["text"],"rows":[["***"],["***"]],"returned_rows":2,"truncated":false,"elapsed_ms":19,"mode":"client",...}
```

A result is bounded in size as well as in rows:

- **10,000 characters per cell.** A longer text, bytes (shown as hex), array or JSON cell is cut, and the cut shows at its end. `select string_agg(order_number, ',') as numbers from marts.fct_orders` returns one cell of 10,022 characters ending in ` …[cut at 10000 chars]`, and the result is not `truncated` (no row was left out).
- **2,000,000 characters in all.** Rows past the budget are left out, and the result is `truncated`. Ten columns of `lpad('a', 10000, 'b')` over `marts.fct_order_items` (100,000 characters a row) return 20 rows. A full 1,000-row result of the widest mart, `select * from marts.fct_orders`, is about 250,000 characters, well inside it.

When a query fails, the red box under the editor shows the guard's or Postgres's message (data errors reworded, see below the table):

| You ran | You see |
|---|---|
| `delete from marts.dim_customers` | Query not allowed: `[guard:not_select] the statement must be a SELECT query, not DELETE; write one SELECT` |
| `select * from public.x` | Query not allowed: `[guard:table] table public.x is outside marts; query only the marts that list_tables returns` |
| `select 1/0 from marts.dim_customers` | Query failed: `a value could not be converted or computed (DivisionByZero); wrap the denominator in nullif(x, 0)` |
| `select email::int from marts.dim_customers` | Query failed: `a value could not be converted or computed (InvalidTextRepresentation); check the literal format` |
| a query slower than the timeout | Query timed out: `the query ran longer than 30 s and was stopped; filter or aggregate more narrowly`, plus a hint to add a `WHERE` or `LIMIT` |
| a 9th query while 8 others still run (in any tab or browser) | `too many queries are running (at most 8 at once); wait for one to finish or cancel one` |

For Postgres errors that name a position (a syntax error, say), the editor highlights that spot in your SQL. With "Run selection" the highlight is placed inside the selection.

A failed cast or computation (a Postgres data exception) never shows the value Postgres complained about: its message would quote it (`invalid input syntax for type integer: "<the email>"`), and the value can be one the masking hides. You get the kind of error and a remedy instead, as in the `1/0` and `email::int` rows above, and the highlight still points at the spot when Postgres names one (`select 'abc'::int` highlights `'abc'`).

## Sorting and filtering

Click a column header to sort (click again to reverse). The filter bar above the grid adds filters with the operators `=`, `!=`, `<`, `<=`, `>`, `>=`, `contains` (case-insensitive), `is null` and `is not null`.

Where it runs depends on the result:

- **Not truncated** (fewer rows than the cap): the browser sorts and filters the rows it already has. Nothing is sent to the server, and the status line shows `12 of 340 rows` while a filter is on.
- **Truncated** (the row cap or the size budget was hit): the rows in the grid are only the first ones, so sorting them would mislead. The Explorer re-sends the SQL of the last run with the sort and filters, and the server applies them over all rows (`select * from (<your sql>) as q where ... order by ...`). A blue note says "Truncated at 1,000 rows: sorting and filtering run on the server over all rows". The wrapped statement goes through the same guarded, read-only path.
- **Masked columns** (`email`, `first_name`, `last_name`, see below): on a truncated result the server would sort and filter by the real values, and the order or the rows that match would give them away (a `contains` filter on `email` would test addresses one guess at a time). So the server refuses: sorting or filtering a truncated result by a masked column fails with `column 'email' holds personal data, shown as ***; the server does not sort or filter by it, since the order or the matching rows would reveal the hidden values`. On a result that is not truncated the browser sorts and filters the `***` it has, which reveals nothing.
- **Duplicate column names** (for example `select o.order_id, i.order_id ...`): the server cannot wrap a result with two columns of one name, so sorting and filtering stay in the browser, over the rows shown, and a yellow "Duplicate column names" banner says so. Alias the columns (`i.order_id as item_order_id`) to get server-side sorting back.

The browser compares values the way Postgres does, so a result gives the same rows and order in either mode:

- **Numbers** compare numerically. A value that is not a number (`abc`, or only spaces) matches nothing, as Postgres would refuse it.
- **Booleans** read a filter value like Postgres: `t`, `true`, `yes`, `on`, `1` and `f`, `false`, `no`, `off`, `0`, in any case. `false` sorts before `true`.
- **Dates and timestamps** take `2025-01-15`, `2025-01-15 10:30` or `2025-01-15T10:30:00`, with an optional offset (`+02`, `+02:00`, `Z`). Without an offset the time is UTC, the server's zone, not your browser's.
- **Text** sorts by code point, as the warehouse's Postgres does: `B` and `Z` come before `a`, and `é` after `z`.
- **`contains`** matches the text the grid shows, which is Postgres's text form: a timestamp reads `2025-01-15 08:30:00.12+00` (not `2025-01-15T08:30:00.120000+00:00`) and a JSON value `{"a": 1}`. Two forms still differ between the modes: a Postgres array shows as `[1, 2]`, where the server matches `{1,2}`, and a JSON number such as `2.0` shows as `2`.
- **NULLs** come last ascending and first descending, and match no comparison (not even `!=`).

## What you can't do, and why

- **Write anything.** Only one `SELECT` or `WITH ... SELECT` statement passes the SQL guard, the same one the agent uses (`sherpa_mcp.sql_guard`). `INSERT`, `UPDATE`, `DELETE`, DDL, `SET` and several statements in one go are rejected with the guard's reason. Behind the guard, the `sherpa_ui` login is read-only at the database too (`default_transaction_read_only = on`), so the guard is not the only line.
- **Read anything but the marts.** Only `marts.<table>` for the nine marts, with real columns. Staging, intermediate, seeds, raw and the `ops` audit table are rejected by the guard (the `sherpa_ui` role can read `ops.dbt_run_results`, but the guard keeps your queries out of it).
- **Get more than 1,000 rows, or more than 2,000,000 characters.** The row cap is 1,000 (a 1,001st row marks the result `truncated`), each cell is cut at 10,000 characters, and rows past 2,000,000 characters in all are left out. Aggregate, select fewer or shorter columns, add `LIMIT` or `WHERE`, or use server-side sort and filter to find the rows you need.
- **Run more than 8 queries at once.** The `sherpa_ui` login may hold 10 connections, and the UI keeps two of them free so that its health check still works while every worksheet is busy. A 9th query is refused at once, without touching the database (see the table above); it runs once you wait for one of the others or cancel it.
- **Run longer than 30 seconds.** The timeout is `SHERPA_UI_STATEMENT_TIMEOUT_S` (whole seconds, default 30, at least 1), set on every connection. Set it in `.env` (`SHERPA_UI_STATEMENT_TIMEOUT_S=60`); `make up` passes it to the `ui` container and `make ui-dev` reads it. The row cap works the same way (`SHERPA_UI_ROW_CAP`).
- **See personal data.** Result columns named `email`, `first_name` or `last_name` (the columns flagged `meta.pii` in the marts) show `***`. The masking is by the result column's name, as for the agent (agent spec section 4.2): `select email as e ...` returns the real address under the name `e`, and so does `lower(email)`. Sorting and filtering on the server refuse the masked columns (see Sorting and filtering). It stops accidents, not a determined reader; the data is synthetic.
- **Keep work on the server.** Nothing you write is saved server-side, and the UI has no accounts. Worksheets and history are per browser.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "The SQL Sherpa server is unreachable" | The `ui` container is down: `docker compose ps`, then `make up` |
| "Database unavailable: queries and the table preview cannot run" | Postgres is down or the `sherpa_ui` login is wrong: `docker compose up -d --wait postgres`; check `SHERPA_UI_PASSWORD` in `.env`, and run `make roles` if the volume predates the role |
| "dbt artifacts missing" | Run `make build-prod`, then reload |
| `the warehouse refused a connection: too many are open for the UI's login (...)` | Another client of the `sherpa_ui` login holds connections: a second UI (`make ui-dev` next to the compose `ui` service) or a test run. Wait for its queries to finish or stop it, then run again |
| Queries fail with a permission error on `marts` | The read grants were lost: `make grant-read` |
| `make up` cannot bind the port | Another process holds 8090: set `SHERPA_UI_PORT` to a free port |

## Next

- [Querying the marts](querying-the-marts.md): what each mart answers, with queries to paste into the Explorer.
- [Extending the UI](../advanced/extending-the-ui.md): add an endpoint or a screen.
