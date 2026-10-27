# Using the Explorer

The Explorer is the web app's first screen: a tree of the marts on the left, a SQL editor with worksheets in the middle, and an Inspector for the selected table on the right (a drawer over the editor below 1280 px). Everything it runs goes to the prod warehouse through a read-only path ([UI spec §5](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md), [§6.4](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md)). It assumes the prod warehouse is built ([Building the warehouse](building-the-warehouse.md)).

## Starting it

```bash
make up           # starts postgres, mcp and ui; waits until all are healthy
make build-prod   # the prod warehouse and the dbt docs the UI reads
```

Open <http://127.0.0.1:8090>; the root redirects to `/explorer`. A link `/explorer?table=<name>` (Lineage's "Open in Explorer") selects that table in the Inspector; a name that is not a mart shows a dismissible "No mart named ..." alert above the editor.

- Before `make build-prod` the page shows a yellow "dbt artifacts missing" banner and the tree offers Retry. A reload picks up a later build; no restart needed.
- The port is `SHERPA_UI_PORT` (default 8090). Put it in `.env` if 8090 is taken.
- For development (Node 22+, `cd ui/frontend && npm ci` once): `make ui-dev` runs the API with reload plus Vite on <http://localhost:5173>. Stop the compose service first (`docker compose stop ui`) or use another port: `make ui-dev SHERPA_UI_PORT=8092`. See [Extending the UI](../advanced/extending-the-ui.md).

## The catalog tree

The tree is `sherpa > marts > <domain> > table > column`: descriptions from dbt's `manifest.json`, column types from `catalog.json`. It lists every mart.

- **Search** filters tables and columns by name.
- **Columns** show their type, a PK badge and a PII badge (masked values).
- **Hover** a table for the first sentence of its description, its grain and primary key.
- **Click** a table to show it in the Inspector: description, domain, grain, primary key, synonyms, columns (with accepted values and the metrics they feed) and dbt tests.
- **Table menu** (right-click, or the "..." button):
  - **Preview 100 rows** opens a worksheet with `select * from marts.<table> limit 100` and runs it.
  - **Insert SELECT** appends a `select *` skeleton to the active worksheet.
  - **Copy name** copies `marts.<table>`.
  - **Open in Lineage** opens the table's graph with its ancestors and descendants (`/lineage?select=+<table>+`; [Using Lineage](using-lineage.md)). The Inspector has the same button.

## Worksheets, history and running

- Worksheets are tabs, each with its own SQL, undo history and running query. Add with the plus button, rename by double-click (or F2), close with the x (or Delete). Switching tabs never cancels a query.
- The editor completes table and column names. Tab indents; press Escape then Tab to leave it.
- **History** (top right of the editor) lists the last 200 runs: SQL, time, rows, status. Picking one loads its SQL into the active worksheet.
- Worksheets and history live in the browser's `localStorage` (`sherpa.v1.explorer`): they survive a reload, belong to one browser, and never include result rows.

| Button | Does |
|---|---|
| **Run** (Ctrl/Cmd+Enter) | Sends the whole worksheet; disabled when empty |
| **Run selection** | Sends only the selected text |
| **Format** | Reformats the SQL (Postgres dialect); SQL that does not parse is left alone and the reason shown |
| **Cancel** | Replaces Run while a query runs; stops it in Postgres ("Cancelled after N s") |

While the database is down (red "Database unavailable" banner), Run, Run selection and Preview are disabled. The health check runs every 30 s, so they come back on their own.

### Reading the result

Above the grid the status line reads `2 rows in 19 ms` with a `read-only` badge, plus an orange `truncated` badge when rows were left out (row cap or size budget, see below).

A failed query shows the guard's or Postgres's message in a red box. A Postgres error that names a position is highlighted in the editor. Data errors (a bad cast, `1/0`) get the kind of error and a remedy, never the offending value, since it could be masked data.

## Sorting and filtering

Click a column header to sort (again to reverse). The filter bar supports `=`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `is null` and `is not null`; a value that does not fit the column's type is refused before anything runs.

- **Not truncated:** the browser sorts and filters the rows it has. Nothing is sent to the server.
- **Truncated:** the grid holds only the first rows, so the Explorer re-sends your SQL wrapped with the sort and filters, and the server applies them over all rows. The same guarded, read-only path runs it, and these re-runs are not history entries.
- **Masked columns** (`email`, `first_name`, `last_name`) are never sorted or filtered, in either mode, because the order or matches would reveal the hidden values. The filter bar lists them as `email (masked)`, disabled.
- **Duplicate column names** (`select o.order_id, i.order_id ...`) cannot be wrapped, so sorting and filtering stay in the browser. Alias the columns to get server-side back.

## What you can't do, and why

- **Write anything.** Only one `SELECT` or `WITH ... SELECT` passes the SQL guard (`sherpa_mcp.sql_guard`, shared with the agent). The `sherpa_ui` login is also read-only at the database (`default_transaction_read_only = on`).
- **Read anything but the marts.** Only `marts.<table>`; staging, intermediate, seeds, raw and `ops` are rejected.
- **Get more than 1,000 rows.** The 1,001st row marks the result `truncated`. A cell is cut at 10,000 characters and rows past 2,000,000 characters in all are left out. Aggregate, select fewer columns, or sort and filter on the server. The cap is `SHERPA_UI_ROW_CAP`.
- **Run longer than 30 s.** The timeout is `SHERPA_UI_STATEMENT_TIMEOUT_S` (whole seconds, default 30). Set either variable in `.env`; `make up` passes it on.
- **Run more than 8 queries at once.** The `sherpa_ui` login holds 10 connections and 2 stay free for the health check. A 9th query is refused at once; wait or cancel one.
- **See personal data.** Result columns named `email`, `first_name` or `last_name` show `***`. Masking is by result-column name, so `select email as e ...` or `lower(email)` returns the real value. It stops accidents only; the data is synthetic.
- **Keep work on the server.** Nothing is saved server-side; there are no accounts.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "The SQL Sherpa server is unreachable" | The `ui` container is down: `docker compose ps`, then `make up` |
| "Database unavailable" banner | Start Postgres (`docker compose up -d --wait postgres`) and check `SHERPA_UI_PASSWORD` in `.env`; `make up` re-applies the roles |
| "dbt artifacts missing" / `catalog_unavailable` in the tree | `make build-prod`, then reload or Retry |
| "Query not allowed: ..." | The guard rejected it: write one `SELECT` over `marts.<table>` |
| "Query timed out ... 30 s" | Add a `WHERE` or `LIMIT`, or aggregate; or raise `SHERPA_UI_STATEMENT_TIMEOUT_S` |
| "Too many queries running" | Wait for one to finish or cancel one (any tab or browser counts) |
| "the warehouse refused a connection: too many are open" | Another client of the `sherpa_ui` login (a second UI, a test run) holds connections; stop it |
| Permission error on `marts` | The read grants were lost: `make build-prod` re-applies them |

## Next

- [Querying the marts](querying-the-marts.md): queries to paste into the Explorer.
- [Using Lineage](using-lineage.md): the graph behind a table.
- [Extending the UI](../advanced/extending-the-ui.md): add an endpoint or a screen.
