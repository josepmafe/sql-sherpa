# Using the MCP server

`sherpa-mcp` is a read-only MCP server over the marts. It gives an agent (or you, through MCP Inspector or a few lines of Python) eight tools: find the right tables, read their documentation, look up how a value is spelled, fetch the metric definitions, see worked examples, and check and run one SELECT. This guide shows how to start it, how to connect, every tool with one real call, what the three kinds of failure look like, and what the PII masking does and does not do. It assumes the prod warehouse is built ([Building the warehouse](building-the-warehouse.md), `make build-prod`).

Outputs below are real and trimmed (`...` marks cut text); the numbers depend on the seed and the dates you simulated.

## What it guarantees

A query can only read the marts, and nothing can be written. Four things enforce that, and Postgres adds two of its own refusals:

1. **The guard** (`sql_guard`) parses the SQL and rejects anything but one SELECT over `marts.<mart>` tables with allow-listed functions.
2. **The role**: the server connects as `sherpa_agent`, which can `SELECT` on the marts and nothing else.
3. **The connection options**: every session starts with `default_transaction_read_only=on` and `statement_timeout=5s`.
4. **`SET LOCAL statement_timeout`**: every transaction sets the 5 s timeout itself, whatever the role defaults say.

On top of those, every query runs over Postgres' extended protocol on a server-side cursor, so Postgres itself refuses multi-statement text and `DECLARE CURSOR` even if the guard and Postgres ever split a query differently. At most 100 rows come back, flagged `truncated` when there were more.

## Starting it

The server reads its settings from `.env` (`SHERPA_DB_*`, `SHERPA_AGENT_*`, `SHERPA_DBT_TARGET_PATH`, `MCP_TRANSPORT`, `SHERPA_MCP_PORT`) and needs two dbt artifacts in `data-platform/dbt/target-prod/`: `manifest.json` from `make build-prod` and `catalog.json` from `make docs-prod` (it is written when the docs are generated; stop the docs server with Ctrl+C once it is up).

**In the container, with everything else.** `make up` builds and starts every compose service, Postgres and `mcp`, and waits until both are healthy. The `mcp` service publishes `127.0.0.1:8765` (localhost only) and mounts `data-platform/dbt/target-prod` read-only. Its environment lists only what it needs (the database host and name, the `sherpa_agent` login, the transport, the port, the artifacts path and an optional `LOGFIRE_TOKEN`), never the admin login. Check it:

```
$ docker compose ps
NAME                    IMAGE                COMMAND                  SERVICE    CREATED         STATUS                   PORTS
sql-sherpa-mcp-1        sql-sherpa-mcp       "python -m sherpa_mc…"   mcp        9 seconds ago   Up 6 seconds (healthy)   127.0.0.1:8765->8765/tcp
sql-sherpa-postgres-1   postgres:16-alpine   "docker-entrypoint.s…"   postgres   3 hours ago     Up 3 hours (healthy)     127.0.0.1:5432->5432/tcp
```

```
$ docker compose logs mcp
mcp-1  | 2026-10-24 14:24:45,553 INFO __main__: serving the warehouse tools over http
mcp-1  | INFO:     Started server process [1]
mcp-1  | INFO:     Waiting for application startup.
mcp-1  | 2026-10-24 14:24:45,624 INFO mcp.server.streamable_http_manager: StreamableHTTP session manager started
mcp-1  | INFO:     Application startup complete.
mcp-1  | INFO:     Uvicorn running on http://0.0.0.0:8765 (Press CTRL+C to quit)
```

Inside the container the server binds `0.0.0.0` and accepts only the Host names `127.0.0.1:*`, `localhost:*`, `[::1]:*` and `mcp:*` (DNS-rebinding protection), so another container on the compose network reaches it as `http://mcp:8765/mcp`. After a code change, `docker compose up -d --build --wait --no-deps mcp` rebuilds and restarts only this service. `make down` stops both services.

**On the host over HTTP.** `make mcp-dev` runs the server from your checkout (Postgres must be up) on `127.0.0.1:8765`; set `SHERPA_MCP_PORT` for another port, for example when the container already holds 8765:

```
$ SHERPA_MCP_PORT=8766 make mcp-dev
MCP_TRANSPORT=http uv run --env-file .env sherpa-mcp
2026-10-24 16:31:34,326 INFO sherpa_mcp.server: serving the warehouse tools over http
INFO:     Started server process [9192]
INFO:     Waiting for application startup.
2026-10-24 16:31:34,368 INFO mcp.server.streamable_http_manager: StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8766 (Press CTRL+C to quit)
```

In HTTP mode the uvicorn access log (`INFO:     127.0.0.1:52784 - "POST /mcp HTTP/1.1" 200 OK`) goes to stdout, which is not the protocol channel there. Stop with Ctrl+C. On the host the server binds `127.0.0.1`, and the SDK's DNS-rebinding protection allows loopback Host names only.

**Over stdio, for a client that starts it.** `MCP_TRANSPORT=stdio` (the default when the variable is unset) makes the server speak JSON-RPC on stdin and stdout. A client starts it as a subprocess: command `uv`, arguments `run sherpa-mcp`, working directory the repository root. The SDK's stdio client passes only a minimal environment to the subprocess, so give it the variables explicitly: `SHERPA_DB_HOST`, `SHERPA_DB_PORT`, `SHERPA_DB_NAME`, `SHERPA_AGENT_USER`, `SHERPA_AGENT_PASSWORD`, `SHERPA_DBT_TARGET_PATH`, `MCP_TRANSPORT=stdio` (and `PATH`; on Windows `SYSTEMROOT`). The server also reads `.env` from its working directory as a fallback. In stdio mode nothing but protocol messages is written to stdout; logs go to stderr.

## Connecting MCP Inspector

MCP Inspector (`npx @modelcontextprotocol/inspector`) is a browser UI for calling MCP tools by hand. With the container or `make mcp-dev` running:

1. Set the transport type to **Streamable HTTP**.
2. Set the URL to `http://127.0.0.1:8765/mcp` (the port of `SHERPA_MCP_PORT`).
3. Connect, open the **Tools** tab and **List Tools**: the eight tools below appear. Pick one, fill in the arguments and run it.

Inspector can also start the server itself over stdio: transport type **STDIO**, command `uv`, arguments `run sherpa-mcp`, and the environment variables above.

## Calling it from Python

The same calls from a script, with the MCP SDK the server uses (`uv run python script.py` from the repository root):

```python
import asyncio
from mcp import Client


async def main() -> None:
    async with Client("http://127.0.0.1:8765/mcp") as client:
        result = await client.call_tool("get_context", {})
        print(result.content[0].text)


asyncio.run(main())
```

Every tool returns JSON text (and the same data as structured content). A failed call has `result.is_error` set and the message as its text.

## The eight tools

### `get_context`

The warehouse's as-of date (the latest order date, to use instead of today for "last month"), the first order date, the domains, and what the warehouse covers and does not. A question that needs something under `does_not_cover` should be answered with "the warehouse cannot answer that".

```
get_context {}
{
  "as_of_date": "2026-10-23",
  "first_order_date": "2025-10-25",
  "domains": ["customers", "products", "sales", "shared"],
  "covers": [
    "customers: dim_customers",
    "products: dim_products",
    "sales: dim_promotions, fct_order_discounts, fct_order_items, fct_orders, fct_refunds, fct_returns",
    "shared: dim_date"
  ],
  "does_not_cover": [
    "inventory and stock levels",
    "product reviews",
    "currencies other than EUR",
    "web sessions (not yet loaded)",
    "marketing campaigns and ad spend (not yet loaded)",
    "leads (not yet loaded)"
  ]
}
```

### `list_tables`

Every mart the model may query, with description, domain, grain and primary key. The list comes from the dbt artifacts, so a new mart appears after a catalog reload (see below).

```
list_tables {}
{"tables": [
  {"name": "dim_customers",
   "description": "Customer dimension, one row per customer except test accounts, with their order history ...",
   "domain": "customers", "grain": "one row per customer", "primary_key": ["customer_id"]},
  {"name": "dim_date", "description": "Calendar dimension, one row per day ...", "domain": "shared",
   "grain": "one row per calendar day", "primary_key": ["date_day"]},
  ...
]}
```

### `describe_table`

One mart in detail: grain, primary key, and per column the data type, description, whether it is PII, the accepted values (when the column has a fixed set) and synonyms; plus three sample rows ordered by primary key, with PII masked. The table name is case-insensitive.

```
describe_table {"table": "dim_customers"}
{
  "name": "dim_customers",
  "grain": "one row per customer",
  "primary_key": ["customer_id"],
  "columns": [
    {"name": "customer_id", "data_type": "bigint", "description": "The customer. Primary key.",
     "is_pii": false, "accepted_values": null, "synonyms": []},
    {"name": "email", "data_type": "text", "description": "Normalized (trimmed, lower-case) email address; null when the customer is deleted.",
     "is_pii": true, "accepted_values": null, "synonyms": []},
    ...
    {"name": "customer_segment", "data_type": "text", "description": "Lifecycle segment: prospect (no non-cancelled order), new ...",
     "is_pii": false, "accepted_values": ["prospect", "new", "active", "at_risk", "churned"],
     "synonyms": ["lifecycle stage", "segment"]},
    ...
  ],
  "sample_rows": {
    "columns": ["customer_id", "email", "first_name", "last_name", "country_code", ...],
    "rows": [[1, "***", "***", "***", "NL", "Netherlands", "Benelux", "2025-10-24T22:22:46+00:00", ...], ...],
    ...
  }
}
```

### `search_values`

Finds how a value is stored in a text column, by trigram similarity (`pg_trgm`, threshold 0.2): up to `limit` (1 to 50, default 10) distinct stored values, best first, with similarity and row count. Use it before filtering on a name the user typed. PII columns and non-text columns are refused.

```
search_values {"table": "dim_products", "column": "brand", "term": "haldin", "limit": 3}
{"matches": [{"value": "Halden", "similarity": 0.4, "row_count": 78}]}

search_values {"table": "dim_customers", "column": "email", "term": "x"}
Error executing tool search_values: dim_customers.email holds PII and cannot be searched
```

### `get_metric_definitions`

The official metric definitions (`aov`, `cogs`, `discount_amount`, `gross_margin`, `gross_revenue`, `net_revenue`, `refund_rate`, `refunded_revenue`), written once in the dbt doc blocks. A question that names a metric must follow them.

```
get_metric_definitions {}
{"metrics": [
  {"name": "aov", "definition": "Average order value (AOV): Σ net_revenue / count of non-cancelled orders, in EUR."},
  {"name": "cogs", "definition": "Cost of goods sold (COGS): Σ quantity × unit cost at the time of sale, in EUR. Returned COGS is ..."},
  ...
]}
```

### `get_examples`

The `k` (1 to 10, default 3) worked examples most similar to a question, ranked by word overlap (Jaccard similarity on the words of the question, stop words removed). Each has the question, the SQL and notes. Adapt them; do not copy them blindly.

```
get_examples {"question": "top products by revenue", "k": 1}
{"examples": [{
  "question": "Which 10 products bring in the most net revenue?",
  "sql": "select\n    p.product_id,\n    p.product_name,\n    sum(i.net_amount - i.refunded_revenue) as net_revenue\nfrom marts.fct_order_items as i\ninner join marts.dim_products as p on p.variant_id = i.variant_id\nwhere not i.is_cancelled\ngroup by p.product_id, p.product_name\norder by net_revenue desc\nlimit 10\n",
  "notes": "dim_products has one row per variant, so group by product_id; line net revenue is net_amount minus refunded_revenue."
}]}
```

### `validate_sql`

Checks a query without running it: the guard first, then a Postgres `EXPLAIN` (no execution). It returns `ok`, the `errors` to fix and the `tables` the query reads. A bad query is a normal result here, not a tool error.

```
validate_sql {"sql": "select current_status, count(*) as orders from marts.fct_orders group by 1 order by 1"}
{"ok": true, "errors": [], "tables": ["marts.fct_orders"]}

validate_sql {"sql": "select * from fct_orders"}
{"ok": false, "errors": ["table fct_orders is not schema-qualified; qualify it as marts.fct_orders"], "tables": []}

validate_sql {"sql": "select order_status, count(*) from marts.fct_orders group by 1"}
{"ok": false, "errors": ["column \"order_status\" does not exist"], "tables": ["marts.fct_orders"]}
```

### `run_sql`

Runs one SELECT exactly as written (a trailing `;` is dropped) and returns the columns and at most 100 rows. `truncated` is true when there were more rows, and `returned_rows` is never a total: aggregate or filter instead. `execution_id` is the first 16 characters of the SHA-256 of the executed SQL.

```
run_sql {"sql": "select current_status, count(*) as orders from marts.fct_orders group by 1 order by 1;"}
{
  "execution_id": "51d5a285dca6768b",
  "columns": ["current_status", "orders"],
  "rows": [["cancelled", 411], ["delivered", 7316], ["paid", 33], ["shipped", 56]],
  "returned_rows": 4,
  "truncated": false,
  "elapsed_ms": 5
}
```

Money comes back as strings (`"6.99"`), dates as ISO text. Over the row cap:

```
run_sql {"sql": "select order_id, customer_id from marts.fct_orders"}
{"execution_id": "e26de08fd4f1a14f", "columns": ["order_id", "customer_id"], "rows": [[4, 1], [6, 1], ...],
 "returned_rows": 100, "truncated": true, "elapsed_ms": 6}
```

## When something goes wrong

A failed `run_sql` (and any other tool) returns `is_error` with a message written for the model to act on.

**A guard rejection.** The message names the rule. Fix the SQL and call again:

```
run_sql {"sql": "delete from marts.fct_orders"}
Error executing tool run_sql: the statement must be a SELECT query, not DELETE

run_sql {"sql": "select c from marts.dim_customers c"}
Error executing tool run_sql: whole-row reference `c` is not allowed; select named columns

run_sql {"sql": "select pg_sleep(10)"}
Error executing tool run_sql: function pg_sleep is not allowed

run_sql {"sql": "select column_name from information_schema.columns limit 1"}
Error executing tool run_sql: table information_schema.columns is outside marts
```

The guard is strict and fails closed: it would rather reject a harmless query than let a risky one through, and it never rewrites the SQL. What models commonly try, and what to write instead:

| Rejected | Message (excerpt) | Write instead |
|---|---|---|
| `col = any(array['FR','DE'])` | `function array is not allowed` | `col in ('FR', 'DE')` |
| `col ~ 'regex'` | `function regexp_like is not allowed` | `ilike`, or `similarity()` |
| `col ->> 'key'`, `@>` (JSON operators) | `function json_extract_scalar is not allowed` | no JSON columns in the marts; select the column |
| `grouping(...)` | `function grouping is not allowed` | plain `group by` |
| `localtimestamp` | `function localtimestamp is not allowed` | `now()` or `current_timestamp` (but prefer the as-of date from `get_context`) |
| `col collate "C"` | `function collate is not allowed` | drop it |
| `(select ...)` as the whole query | `the statement must be a SELECT query, not SUBQUERY` | remove the outer parentheses |
| `from generate_series(...)`, also `public.generate_series(...)` | `function exploding_generate_series is not allowed` | join `marts.dim_date` for a series of days |
| `from fct_orders` | `table fct_orders is not schema-qualified; qualify it as marts.fct_orders` | `from marts.fct_orders` |
| `select o from marts.fct_orders o` | `whole-row reference `o` is not allowed` | name the columns |
| `x::regclass` and other `reg*` or non-standard types | `cast to type REGCLASS is not allowed` | cast to standard types (`int`, `numeric`, `text`, `date`, ...) |
| two statements | `expected exactly one statement, got 2` | one statement |

The allowed functions are the aggregates, window functions, math, string, date and conditional functions in `ALLOWED_FUNCTIONS` in `agent/mcp-server/src/sherpa_mcp/sql_guard.py`, plus `similarity`.

**A timeout.** Queries are cancelled after 5 s, and the connection stays usable. `pg_sleep` is not allowed, so the example is a three-way self-join of a 13,711-row table:

```
run_sql {"sql": "select count(*) from marts.fct_order_items a cross join marts.fct_order_items b cross join marts.fct_order_items c"}
Error executing tool run_sql: the query took longer than 5 s; aggregate or filter more narrowly
```

**A data error.** Postgres data exceptions (class 22: a failed cast, a division by zero, ...) normally quote the offending value, which could be PII, so the server returns them without it:

```
run_sql {"sql": "select email::int from marts.dim_customers limit 1"}
Error executing tool run_sql: a value could not be converted or computed (InvalidTextRepresentation); check the column types and use explicit, safe casts
```

**Missing artifacts.** Without `manifest.json` and `catalog.json` in `SHERPA_DBT_TARGET_PATH`, every tool that needs the catalog fails and says what to run. (This run pointed a one-off server at an empty folder.)

```
list_tables {}
Error executing tool list_tables: dbt artifacts not found (manifest.json, catalog.json): run `make build-prod` and `make docs-prod` (catalog.json is written by `make docs-prod`)
```

Run both and call again: the server notices the new files by their modification time, so it needs no restart. The same happens after any rebuild of the marts.

**An unexpected error.** Anything else (a bug) reaches the model only as `Error executing tool <name>`; the traceback goes to the server's stderr and, with Logfire on, to the trace.

## PII masking

`run_sql` and the `describe_table` samples replace an output column with `***` when the column's **name** is a PII column in any mart (`email`, `first_name`, `last_name`; the flag is `meta.pii` in the dbt YAML). `NULL` stays `NULL`.

```
run_sql {"sql": "select customer_id, email from marts.dim_customers order by 1 limit 2"}
{"columns": ["customer_id", "email"], "rows": [[1, "***"], [2, "***"]], ...}
```

The masking is name-based, and that is an accepted limitation (the data is synthetic and local): it does **not** catch

- an alias: `select email as e` returns the address under the name `e`;
- an expression: `lower(email)`, `email || ''`;
- an aggregate: `string_agg(email, ',')`;
- a set operation that names the column differently;
- LIKE or count oracles: `count(*) ... where email like 'a%'` reveals a fact about the hidden values.

```
run_sql {"sql": "select customer_id, email as e from marts.dim_customers order by 1 limit 2"}
{"columns": ["customer_id", "e"], "rows": [[1, "jurre931@hotmail.com"], [2, "bbeccheria2@virgilio.it"]], ...}

run_sql {"sql": "select string_agg(email, ',') as joined from (select email from marts.dim_customers order by customer_id limit 2) t"}
{"columns": ["joined"], "rows": [["jurre931@hotmail.com,bbeccheria2@virgilio.it"]], ...}
```

Two leaks are closed on purpose: a whole-row reference (`select c from marts.dim_customers c`) would put every PII field into one value, so the guard rejects it; and class 22 errors are returned without the value, as shown above.

## Tracing with Logfire

The server instruments its tool calls, the guard decision (a `sherpa.guard` span) and every database query with Logfire. With no `LOGFIRE_TOKEN`, nothing is exported and nothing is printed. To send traces, put `LOGFIRE_TOKEN=<your write token>` in `.env` (the compose `mcp` service passes it through; it is optional). Two things to know before you do:

- The `sherpa.guard` span carries the tool, whether the query passed, the number of errors, the tables and the `execution_id`, but never SQL text or error texts, which can quote literals.
- The psycopg instrumentation records each query's statement text, which includes the literals in model-written SQL. A literal a user typed (an email address, say) is therefore in the trace.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `dbt artifacts not found` | Run `make build-prod` and `make docs-prod`, as the message says |
| The container is unhealthy or exits | `docker compose logs mcp`; usually the `SHERPA_AGENT_PASSWORD` in `.env` does not match the role, or Postgres is not up |
| `make mcp-dev` cannot start because the port is taken | The container already holds 8765; set `SHERPA_MCP_PORT=8766` |
| A client gets HTTP 421 | The Host header is not an allowed name (`127.0.0.1`, `localhost`, `[::1]`, `mcp`); connect through one of those |
