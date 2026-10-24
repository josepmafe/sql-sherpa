# Using the MCP server

`sherpa-mcp` is a read-only MCP server over the marts. It gives an agent (or you, through MCP Inspector or a few lines of Python) eight tools: find the right tables, read their documentation, look up how a value is spelled, fetch the metric definitions, see worked examples, and check and run one SELECT. This guide shows how to start it, how to connect, every tool with a real call, what failures look like, what the PII masking does and does not do, and the server's known limitations. It assumes the prod warehouse is built ([Building the warehouse](building-the-warehouse.md), `make build-prod`).

Outputs below are real and trimmed (`...` marks cut text); the numbers depend on the seed and the dates you simulated.

## What it guarantees

A query can only read the marts, and nothing can be written. Four things enforce that, and Postgres adds two of its own refusals:

1. **The guard** (`sql_guard`) parses the SQL and rejects anything but one SELECT over `marts.<mart>` tables with allow-listed functions, in which every qualified column is a real column of its source.
2. **The role**: the server connects as `sherpa_agent`, which can `SELECT` on the marts and nothing else.
3. **The connection options**: every session starts with `default_transaction_read_only=on` and `statement_timeout=5s`.
4. **`SET LOCAL statement_timeout`**: every transaction sets the 5 s timeout itself, whatever the role defaults say.

On top of those, every query runs over Postgres's extended protocol on a server-side cursor, so Postgres itself refuses multi-statement text and `DECLARE CURSOR` even if the guard and Postgres ever split a query differently. A result is bounded three ways: at most 100 rows, at most 10,000 characters per cell, and at most 50,000 characters in all; `truncated` says when rows were left out.

## Starting it

The server reads its settings from `.env` (`SHERPA_DB_*`, `SHERPA_AGENT_*`, `SHERPA_DBT_TARGET_PATH`, `MCP_TRANSPORT`, `SHERPA_MCP_HOST`, `SHERPA_MCP_PORT`, `SHERPA_MCP_POOL_SIZE`) and needs two dbt artifacts in `data-platform/dbt/target-prod/`: `manifest.json` from `make build-prod` and `catalog.json` from `make docs-prod`. `make docs-prod` writes `catalog.json` and then serves the prod docs on port 8080; stop it with Ctrl+C once it says it is serving (a `make docs` already running holds the same port).

At startup the server waits up to 15 s for its first warehouse connection. If none opens (Postgres down, wrong password), it logs why on stderr and exits with status 1 instead of serving tools that would all fail:

```
$ SHERPA_DB_PORT=1 uv run --env-file .env sherpa-mcp
2026-10-24 23:02:10,359 INFO sherpa_mcp.server: reading the dbt artifacts from <repo>/data-platform/dbt/target-prod
2026-10-24 23:02:20,376 WARNING psycopg.pool: error connecting in 'pool-1': connection timeout expired
...
2026-10-24 23:02:30,467 ERROR sherpa_mcp.server: cannot connect to the warehouse at 127.0.0.1:1 as sherpa_agent within 15 s; check Postgres is up and the SHERPA_DB_* and SHERPA_AGENT_* variables
```

**In the container, with everything else.** `make up` builds and starts every compose service, Postgres and `mcp`, and waits until both are healthy. The `mcp` service:

- publishes `127.0.0.1:8765` (localhost only). The host port is `SHERPA_MCP_PORT` from `.env`, so setting it there moves the container's published port as well as the host server's; inside the container the server always listens on 8765;
- mounts `data-platform/dbt` read-only at `/app/dbt` and reads the artifacts from `/app/dbt/target-prod`, so a `target-prod` deleted and rebuilt on the host is picked up without a restart;
- has a 1 GB memory limit and `restart: unless-stopped`: if a query ever runs it out of memory, Docker starts it again;
- gets only the variables it needs, never the admin login: `SHERPA_DB_HOST`, `SHERPA_DB_PORT`, `SHERPA_DB_NAME`, `SHERPA_AGENT_USER`, `SHERPA_AGENT_PASSWORD`, `MCP_TRANSPORT`, `SHERPA_MCP_HOST`, `SHERPA_MCP_PORT`, `SHERPA_DBT_TARGET_PATH`, `SHERPA_MCP_POOL_SIZE` and an optional `LOGFIRE_TOKEN`.

```
$ docker compose ps
NAME                    IMAGE                COMMAND                  SERVICE    CREATED         STATUS                   PORTS
sql-sherpa-mcp-1        sql-sherpa-mcp       "python -m sherpa_mc…"   mcp        6 minutes ago   Up 6 minutes (healthy)   127.0.0.1:8765->8765/tcp
sql-sherpa-postgres-1   postgres:16-alpine   "docker-entrypoint.s…"   postgres   9 hours ago     Up 9 hours (healthy)     127.0.0.1:5432->5432/tcp

$ docker compose logs mcp
mcp-1  | 2026-10-24 20:55:26,937 INFO __main__: reading the dbt artifacts from /app/dbt/target-prod
mcp-1  | 2026-10-24 20:55:27,054 INFO __main__: serving the warehouse tools over http
mcp-1  | INFO:     Started server process [1]
mcp-1  | INFO:     Waiting for application startup.
mcp-1  | 2026-10-24 20:55:27,168 INFO mcp.server.streamable_http_manager: StreamableHTTP session manager started
mcp-1  | INFO:     Application startup complete.
mcp-1  | INFO:     Uvicorn running on http://0.0.0.0:8765 (Press CTRL+C to quit)
mcp-1  | INFO:     172.18.0.1:49950 - "POST /mcp HTTP/1.1" 200 OK

$ docker inspect sql-sherpa-mcp-1 --format 'restart={{.HostConfig.RestartPolicy.Name}} memory={{.HostConfig.Memory}}'
restart=unless-stopped memory=1073741824

$ SHERPA_MCP_PORT=8766 docker compose config
...
        host_ip: 127.0.0.1
        target: 8765
        published: "8766"
...
```

Inside the container the server binds `0.0.0.0` and accepts only the Host names `127.0.0.1:*`, `localhost:*`, `[::1]:*` and `mcp:*` (DNS-rebinding protection), so another container on the compose network reaches it as `http://mcp:8765/mcp`. The HTTP endpoint has no authentication: keep the published port on `127.0.0.1`, and never publish it on `0.0.0.0` or another interface. After a code change, `docker compose up -d --build --wait --no-deps mcp` rebuilds and restarts only this service. `make down` stops both services.

**On the host over HTTP.** `make mcp-dev` runs the server from your checkout (Postgres must be up) on `127.0.0.1:8765`. When the container already holds 8765, give another port on the command line:

```
$ SHERPA_MCP_PORT=8766 make mcp-dev
MCP_TRANSPORT=http uv run --env-file .env sherpa-mcp
2026-10-24 23:01:54,930 INFO sherpa_mcp.server: reading the dbt artifacts from <repo>/data-platform/dbt/target-prod
2026-10-24 23:01:54,998 INFO sherpa_mcp.server: serving the warehouse tools over http
INFO:     Started server process [33636]
INFO:     Waiting for application startup.
2026-10-24 23:01:55,038 INFO mcp.server.streamable_http_manager: StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8766 (Press CTRL+C to quit)
```

In HTTP mode the uvicorn access log goes to stdout, which is not the protocol channel there. Stop with Ctrl+C. On the host the server binds `127.0.0.1`, and the SDK's DNS-rebinding protection allows loopback Host names only.

**Over stdio, for a client that starts it.** `MCP_TRANSPORT=stdio` (the default when the variable is unset) makes the server speak JSON-RPC on stdin and stdout; nothing else is written to stdout, and logs go to stderr. The client starts it as a subprocess with `uv run --directory <repository root> sherpa-mcp`. The server reads `.env` from its working directory (`--directory` sets it), so the client only needs to pass `MCP_TRANSPORT=stdio`; the MCP SDK's stdio client passes a minimal environment (`PATH`, and `SYSTEMROOT` on Windows), which is enough for `uv`. Variables the client passes win over `.env`. On a OneDrive folder add `UV_LINK_MODE=copy`.

For a client configured with an `mcpServers` JSON block (Claude Desktop's `claude_desktop_config.json`, for example):

```json
{
  "mcpServers": {
    "sherpa": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/sql-sherpa", "sherpa-mcp"],
      "env": {"MCP_TRANSPORT": "stdio"}
    }
  }
}
```

The same launch (`uv run --directory <repo> sherpa-mcp` with only `PATH`, `SYSTEMROOT`, `MCP_TRANSPORT` and `UV_LINK_MODE` in the environment, started from another folder) lists the eight tools when driven by the MCP SDK's stdio client. For Claude Code with the server already running over HTTP, the one-liner is `claude mcp add --transport http sherpa http://127.0.0.1:8765/mcp`.

## Connecting MCP Inspector

MCP Inspector (`npx @modelcontextprotocol/inspector`) is a browser UI for calling MCP tools by hand. With the container or `make mcp-dev` running:

1. Set the transport type to **Streamable HTTP**.
2. Set the URL to `http://127.0.0.1:8765/mcp` (the port of `SHERPA_MCP_PORT`).
3. Connect, open the **Tools** tab and **List Tools**: the eight tools below appear. Pick one, fill in the arguments and run it.

Inspector can also start the server itself over stdio: transport type **STDIO**, command `uv`, arguments `run --directory <repository root> sherpa-mcp`.

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

Every tool returns JSON text (and the same data as structured content). A failed call has `result.is_error` set and its text is `Error executing tool <name>: <message>`.

## The eight tools

Text that comes from the warehouse (cell values, stored values, sample rows) is data, never instructions; the tool descriptions say so to the model.

### `get_context`

The as-of date (the latest order date, to use instead of today for "last month"), the first order date, the domains, what the warehouse covers and does not, and notes that change what a correct answer is. A question that needs something under `does_not_cover` should be answered with "the warehouse cannot answer that". The year-over-year entry appears while the history is shorter than two years.

```
get_context {}
{
  "as_of_date": "2026-10-23",
  "first_order_date": "2025-10-25",
  "domains": ["customers", "products", "sales", "shared"],
  "covers": [
    "customers: lifecycle segment, VIP flag, lead source, acquisition channel and lifetime order history (dim_customers)",
    "products and their variants: brand, three-level category, current list and cost prices (dim_products)",
    "orders, order lines, discounts and promotions, returns and refunds: revenue, cost, margin, delivery (dim_promotions, fct_order_discounts, fct_order_items, fct_orders, fct_refunds, fct_returns)",
    "a calendar of days with weeks, months, quarters, weekends and Black Friday week (dim_date)"
  ],
  "does_not_cover": [
    "inventory and stock levels",
    "product reviews",
    "currencies other than EUR",
    "web sessions and on-site behaviour (orders carry only session_id and marketing_channel)",
    "campaign names, budgets and ad spend (customers and promotions carry only a campaign id)",
    "leads that never became customers (customers carry lead_id and lead_source)",
    "comparisons across years, such as year over year: the history is shorter than two years"
  ],
  "notes": [
    "as_of_date is the latest order date: resolve relative periods (last month, last 30 days, this year) against it, never against today",
    "the history runs from first_order_date to as_of_date, so the first and the last month are partial",
    "refunds arrive 6 to 47 days after the order and count on the order's date, so for orders of the last 7 weeks refunded revenue is understated and net revenue overstated",
    "amounts are EUR; gross and net revenue exclude the 20% tax and the shipping",
    "dates and timestamps are UTC",
    "cancelled orders are kept and flagged: metrics filter on not is_cancelled",
    "test accounts and their orders are left out of every mart"
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
  ...
]}
```

### `describe_table`

One mart in detail: grain, primary key, and per column the data type, description, whether it is PII, the accepted values (when the column has a fixed set), `distinct_values` (every stored value of a text column that has at most 30 of them; `null` for PII, other types and larger sets), synonyms and `metric_refs` (the metric definitions its description uses); plus three sample rows ordered by primary key, with PII masked. The stored values are read once per dbt build. The table name is case-insensitive.

```
describe_table {"table": "dim_customers"}
{
  "name": "dim_customers",
  "grain": "one row per customer",
  "primary_key": ["customer_id"],
  "columns": [
    {"name": "customer_id", "data_type": "bigint", "description": "The customer. Primary key.", "is_pii": false,
     "accepted_values": null, "distinct_values": null, "synonyms": [], "metric_refs": []},
    {"name": "email", "data_type": "text", "description": "Normalized (trimmed, lower-case) email address; null when the customer is deleted.",
     "is_pii": true, "accepted_values": null, "distinct_values": null, "synonyms": [], "metric_refs": []},
    ...
    {"name": "country_name", "data_type": "text", "description": "...", "is_pii": false,
     "accepted_values": null,
     "distinct_values": ["Austria", "Belgium", "France", "Germany", "Italy", "Netherlands", "Spain", "United Kingdom"],
     "synonyms": ["country"], "metric_refs": []},
    ...
  ],
  "sample_rows": {"columns": ["customer_id", "email", "first_name", "last_name", "country_code", ...],
                  "rows": [[1, "***", "***", "***", "NL", ...], ...]}
}
```

### `search_values`

Finds how a value is stored in a text column with `pg_trgm`: up to `limit` (1 to 50, default 10) distinct stored values whose trigram similarity to the term is at least 0.2, or that contain the term as a word (word similarity at least 0.6), best first. `similarity` is the larger of the two scores. An empty result carries a `hint`. Use it before filtering on a name the user typed. PII columns and non-text columns are refused.

```
search_values {"table": "dim_products", "column": "brand", "term": "haldin", "limit": 3}
{"matches": [{"value": "Halden", "similarity": 0.5714286, "row_count": 78}], "hint": null}

search_values {"table": "dim_products", "column": "product_name", "term": "Halden", "limit": 3}
{"matches": [{"value": "Halden Active Resistance Bands", "similarity": 1.0, "row_count": 2},
             {"value": "Halden Core Duffel Bags", "similarity": 1.0, "row_count": 2},
             {"value": "Halden Core Dumbbells", "similarity": 1.0, "row_count": 2}], "hint": null}

search_values {"table": "dim_customers", "column": "country_name", "term": "UK"}
{"matches": [], "hint": "no stored value has a similarity of 0.2 or more and none contains the term as a word; try a sibling column, an English or code form of the term, or the distinct_values describe_table lists"}

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

Up to `k` (1 to 10, default 3) worked examples, best first, each with its question, SQL, notes and `score`. The words are stemmed (`monthly` and `months` meet `month`), a few synonyms are expanded (`aov`, `sales`, `coupon`, `units`, `yoy`, ...), and the score is a Jaccard similarity weighted 0.75 on the example's question and 0.25 on everything it says (question, notes, and the table and column names of its SQL). Examples sharing no word with the question are left out, so a question unlike all of them gets an empty list. Adapt them; do not copy them blindly.

The `score` is relative: it only orders the examples for one question and says nothing about whether the warehouse can answer it. `get_examples {"question": "Delete all cancelled orders"}` returns "What percentage of placed orders end up cancelled?" with a score of 0.25, higher than the good match in the example below. Whether a question is out of scope comes from `get_context`'s `does_not_cover` (a stock question falls under "inventory and stock levels") and from the server being read-only (the guard rejects any write), never from the score.

```
get_examples {"question": "top products by revenue", "k": 2}
{"examples": [
  {"question": "Which 10 products bring in the most net revenue?",
   "sql": "select\n    p.product_id,\n    p.product_name,\n    sum(i.net_amount - i.refunded_revenue) as net_revenue\n...",
   "notes": "dim_products has one row per variant, so group by product_id; ...", "score": 0.232},
  {"question": "What is our net revenue each month?", "sql": "...", "notes": "...", "score": 0.158}
]}

get_examples {"question": "what is the weather like", "k": 3}
{"examples": []}
```

### `validate_sql`

Checks a query without running it: the guard first, then a Postgres `EXPLAIN` (no execution). It returns `ok`, the `errors` to fix, the `tables` the query reads and `warnings`. A bad query is a normal result here, not a tool error. A warning does not block the query: today it flags a query that reads the real clock (`now()`, `current_timestamp` or `current_date`), which answers for the wrong period because the data stops at the as-of date.

```
validate_sql {"sql": "select current_status, count(*) as orders from marts.fct_orders group by 1 order by 1"}
{"ok": true, "errors": [], "tables": ["marts.fct_orders"], "warnings": []}

validate_sql {"sql": "select * from fct_orders"}
{"ok": false, "errors": ["[guard:table] table fct_orders is not schema-qualified; qualify it as marts.fct_orders"], "tables": [], "warnings": []}

validate_sql {"sql": "select order_status, count(*) from marts.fct_orders group by 1"}
{"ok": false, "errors": ["[sql] column \"order_status\" does not exist (at character 8)"], "tables": ["marts.fct_orders"], "warnings": []}

validate_sql {"sql": "select count(*) from marts.fct_orders where order_date >= current_date - 30"}
{"ok": true, "errors": [], "tables": ["marts.fct_orders"],
 "warnings": ["current_date reads the real clock, but the warehouse is as of 2026-10-23; derive periods from (select max(order_date) from marts.fct_orders)"]}
```

`EXPLAIN` plans the query without running it, so errors that only happen while running usually pass it: `select sum(net_revenue) / 0 from marts.fct_orders` validates with `ok: true` and fails in `run_sql` with a division-by-zero error (below). Planning does fold constant expressions, so `select 1/0 from marts.dim_date` already fails in `validate_sql`, with the same `[sql] ... (DivisionByZero)` error.

### `run_sql`

Runs one SELECT exactly as written (a trailing `;` is dropped) and returns the columns, their Postgres types and at most 100 rows. `truncated` is true when rows were left out, and `returned_rows` is never a total: aggregate or filter instead. `execution_id` is the first 16 characters of the SHA-256 of the executed SQL.

```
run_sql {"sql": "select current_status, count(*) as orders from marts.fct_orders group by 1 order by 1;"}
{
  "execution_id": "51d5a285dca6768b",
  "columns": ["current_status", "orders"],
  "column_types": ["text", "int8"],
  "rows": [["cancelled", 411], ["delivered", 7316], ["paid", 33], ["shipped", 56]],
  "returned_rows": 4,
  "truncated": false,
  "elapsed_ms": 3
}
```

Money comes back as strings (`"6.99"`), dates as ISO text. Rows are fetched 20 at a time (one at a time after a row over 1,000,000 characters) and stop at three limits, each of which sets `truncated` or cuts a cell visibly:

- **100 rows.** `select order_id, customer_id from marts.fct_orders` returns `"returned_rows": 100, "truncated": true`.
- **10,000 characters per cell.** A longer text, bytes (shown as hex), array or JSON cell is cut, and the cut shows at its end: `select string_agg(order_number, ',') as numbers from marts.fct_orders` returns one cell of 10,022 characters ending in `SS-002905, …[cut at 10000 chars]`, with `"truncated": false` (no row was left out).
- **50,000 characters in all.** Rows past the budget are left out: `select lpad('a', 10000, 'b') as n from marts.fct_order_items limit 10` returns 5 rows of 10,000 characters and `"truncated": true`.

At most two queries fetch their rows at the same time; a third waits up to 10 s for a slot. Fetching also stops after 5 s, so a query whose rows are huge ends with the timeout error below rather than a slow, oversized answer.

## When something goes wrong

A failed tool call returns `is_error` with a message written for the model to act on. Over MCP its text is `Error executing tool <name>: <message>`, and every message from the guard or the database starts with a code:

| Code | Meaning | What to do |
|---|---|---|
| `[guard:<rule>]` | The guard rejected the query; the rule is one of `parse`, `statement_count`, `not_select`, `write`, `select_into`, `locking`, `cast`, `function`, `field`, `whole_row`, `column`, `table`, `recursive`, `pad`, `type_length`, `number` | Follow the hint after the `;` and send a new query |
| `[sql]` | Postgres refused or failed the query | Read the message, the position (`at character N`, counted in the query as written) and Postgres's hint, if any |
| `[timeout]` | The query ran over 5 s, or the server was busy with two other queries for 10 s | Aggregate or filter more narrowly, or retry |
| `[unreachable]` | No connection to the warehouse | Start Postgres (`make up`) |
| `[unavailable]` | The dbt artifacts are missing or unreadable | Run `make build-prod` and `make docs-prod` |

Argument errors have no code: an unknown table or column comes with the list of valid names, and `search_values` names the reason it refuses a PII or non-text column.

**A guard rejection.** The message names the rule and how to fix it:

```
run_sql {"sql": "delete from marts.fct_orders"}
Error executing tool run_sql: [guard:not_select] the statement must be a SELECT query, not DELETE; write one SELECT

run_sql {"sql": "select c from marts.dim_customers c"}
Error executing tool run_sql: [guard:whole_row] whole-row reference `c` is not allowed; select named columns

run_sql {"sql": "select c.row_to_json from marts.dim_customers c limit 1"}
Error executing tool run_sql: [guard:column] column c.row_to_json is not a column of marts.dim_customers; use a column listed by describe_table

run_sql {"sql": "select pg_sleep(10)"}
Error executing tool run_sql: [guard:function] function pg_sleep is not allowed; rewrite it with the common aggregate, window, string, date and math functions

run_sql {"sql": "select column_name from information_schema.columns limit 1"}
Error executing tool run_sql: [guard:table] table information_schema.columns is outside marts; query only the marts that list_tables returns
```

The guard is strict and fails closed: it would rather reject a harmless query than let a risky one through, and it never rewrites the SQL. What models commonly try, and the hint they get:

| Rejected | Message (after `Error executing tool run_sql: `) |
|---|---|
| `col = any(array['web'])` | ``[guard:function] function or operator `ARRAY['web']` is not allowed; use IN (...) instead of = ANY(ARRAY[...])`` |
| `sales_channel ~ 'w'` | ``[guard:function] function or operator `sales_channel ~ 'w'` is not allowed; use LIKE or ILIKE instead of a regular expression`` |
| `(select 1)` as the whole query | `[guard:not_select] the statement must be a SELECT query, not SUBQUERY; remove the outer parentheses` |
| `from generate_series(1, 3) d` | `... [guard:function] function generate_series is not allowed; use marts.dim_date for a series of dates` |
| `to_timestamp(0)` | `[guard:function] function to_timestamp is not allowed; use to_char, make_date or date_trunc` |
| `json_agg(order_id)` | `[guard:function] function json_agg is not allowed; aggregate with string_agg, or select the columns` |
| `grouping(sales_channel)` | `[guard:function] function grouping is not allowed; use a plain GROUP BY, or one query per grouping level` |
| `with recursive t(n) as (...)` | `[guard:recursive] WITH RECURSIVE is not allowed; use marts.dim_date for a series of dates, or a non-recursive query` |
| `lpad('a', 100000000, 'b')` | `[guard:pad] lpad needs a literal length of at most 10,000; write the length as a number, e.g. lpad(x::text, 2, '0')` |
| `from fct_orders` | `[guard:table] table fct_orders is not schema-qualified; qualify it as marts.fct_orders` |

`lpad` and `rpad` pass with a literal length up to 10,000 (`lpad(order_id::text, 8, '0')` returns `00000001`); `repeat` is not allowed at all. A type's length or precision is capped the same way, because `char(n)` and `bit(n)` pad every value to `n`: `''::char(10485760)` returns ``[guard:type_length] type CHAR(10485760) has a length over 10,000; use lengths up to 10,000, or cast to text``, while `varchar(255)` and `numeric(12,2)` pass. Numbers must be plain decimal digits: Postgres 16 also reads `10_000`, `0x2710`, `0o23420` and `0b10011100010000` as numbers, but the guard's parser splits or misreads them (it would see `char(10_485_760)` as `char(10)`), so they are rejected with ``[guard:number] numeric literal `10_000` is not allowed; write plain decimal digits such as 10000, without _ separators or a 0x, 0o or 0b prefix``. Digits and underscores inside strings, quoted or plain names (`customer_id`, `'1_000'`) and comments are fine. A qualified column of a derived table or CTE needs a name the guard can see: an alias, a column name, or a bare `count`, `sum`, `avg`, `min` or `max` call, which Postgres names after the function (`select t.count from (select count(*) from marts.fct_orders) t` passes). Alias anything else (`max(order_date)::text as last_day`), and behind an alias list (`marts.dim_customers c(a)`) use only the listed names. Other rejected constructs: `->>` and `@>` (no JSON columns in the marts), `localtimestamp`, `collate`, casts to `reg*` or other non-standard types, a whole-row reference inside an expression (`o.*::text`, `concat(o.*)`), and more than one statement. The allowed functions are the aggregates, window functions, math, string, date and conditional functions in `ALLOWED_FUNCTIONS` in `agent/mcp-server/src/sherpa_mcp/sql_guard.py`, plus `similarity`.

**A database error.** The message is Postgres's, with the position in the query and Postgres's hint when it has one:

```
run_sql {"sql": "select emial from marts.dim_customers c"}
Error executing tool run_sql: [sql] column "emial" does not exist (at character 8); Perhaps you meant to reference the column "c.email".
```

**A data error.** Postgres data exceptions (class 22: a failed cast, a division by zero, ...) quote the offending value, which could be PII, so the server returns the exception's name and a remedy instead:

```
run_sql {"sql": "select sum(net_revenue) / 0 from marts.fct_orders"}
Error executing tool run_sql: [sql] a value could not be converted or computed (DivisionByZero); wrap the denominator in nullif(x, 0)

run_sql {"sql": "select email::int from marts.dim_customers limit 1"}
Error executing tool run_sql: [sql] a value could not be converted or computed (InvalidTextRepresentation); check the literal format
```

**A timeout.** Queries are cancelled after 5 s, and the connection stays usable. `pg_sleep` is not allowed, so the example is a three-way self-join of the order lines:

```
run_sql {"sql": "select count(*) from marts.fct_order_items a cross join marts.fct_order_items b cross join marts.fct_order_items c"}
Error executing tool run_sql: [timeout] the query took longer than 5 s; aggregate or filter more narrowly
```

**Missing artifacts.** Without `manifest.json` and `catalog.json` in `SHERPA_DBT_TARGET_PATH`, every tool that needs the catalog fails with ``[unavailable] dbt artifacts not found (manifest.json, catalog.json): run `make build-prod` and `make docs-prod` (catalog.json is written by `make docs-prod`)``. Run both and call again: the server notices the new files by their modification time, so it needs no restart. The same happens after any rebuild of the marts.

**An unexpected error.** Anything else (a bug) reaches the model only as `Error executing tool <name>`; the traceback goes to the server's stderr and, with Logfire on, to the trace.

## PII masking

`run_sql` and the `describe_table` samples replace an output column with `***` when the column's **name** is a PII column in any mart (`email`, `first_name`, `last_name`; the flag is `meta.pii` in the dbt YAML). `NULL` stays `NULL`.

```
run_sql {"sql": "select customer_id, email from marts.dim_customers order by 1 limit 2"}
{"columns": ["customer_id", "email"], "column_types": ["int8", "text"], "rows": [[1, "***"], [2, "***"]], ...}
```

The masking is name-based, and that is an accepted limitation (the data is synthetic and local): it does **not** catch

- an alias: `select email as e` returns the address under the name `e`;
- an expression: `lower(email)`, `email || ''`;
- an aggregate: `string_agg(email, ',')`;
- a set operation that names the column differently;
- LIKE or count oracles: `count(*) ... where email like 'a%'` reveals a fact about the hidden values.

```
run_sql {"sql": "select customer_id, email as e from marts.dim_customers order by 1 limit 2"}
{"columns": ["customer_id", "e"], "rows": [[1, "<an email address>"], [2, "<an email address>"]], ...}
```

Three leaks are closed on purpose: a whole-row reference (`select c from marts.dim_customers c`, or `c.*` inside an expression such as `c.*::text`) would put every PII field into one value, so the guard rejects it (a plain `select c.*` expands to named columns, which are masked); a qualified name that is not a column (`c.row_to_json`, which Postgres reads as the function `row_to_json(c)`) is rejected for the same reason; and class 22 errors are returned without the value, as shown above.

## Known limitations

What the server does not protect against, and what bounds each case:

1. **Result size inside Postgres.** Postgres cannot cap the size of one value while it builds it. An adversarial `string_agg` or `array_agg` over a cross join can still build a very large value within the 5 s timeout, and so can string functions with no cross join at all: `replace(lpad('a', 10000), ' ', lpad('a', 10000))` passes the guard and builds about 100 MB per row, nesting `replace` multiplies that again, and `||` adds up as many 10,000-character pieces as the query has room for. Bounded by: the guard (no `WITH RECURSIVE`, `lpad`/`rpad` only with a literal length of at most 10,000, type lengths such as `char(n)` and `bit(n)` at most 10,000, numbers only as plain decimal digits so that neither cap can be written as `10_485_760` or `0x989680`, no `repeat`); the 20,000-character limit on the query text; Postgres's own 1 GB limit on one value; the 10,000-character cell cap and the 50,000-character result budget, which cut what reaches the model but not what the server holds while fetching; fetching 20 rows at a time and one at a time after a row over 1,000,000 characters (so the first batch can hold 20 such rows); at most two such fetches at once; the container's 1 GB memory limit with `restart: unless-stopped`, so a result that exceeds it kills and restarts the server, not the host; and the agent role's limits on a fresh volume (below). A server started over stdio runs on the host with no memory limit.
2. **Queries are not wrapped in a truncating subquery.** Wrapping (`select left(col, 10000) ... from (<query>) q`) is the only exact fix for (1), and it is deliberately not done: the query would no longer run exactly as written, so `execution_id` would not identify what ran; `ORDER BY` is not guaranteed through a subquery; duplicate or unnamed result columns would need renaming; it costs an extra round trip to learn the columns; and Postgres would still build the value before cutting it.
3. **Name-based masking** (above): an alias, an expression, an aggregate, a union or a LIKE or count oracle reveals PII. The `sherpa_agent` role can also read the PII columns' stored values in `pg_stats` outside the guard (the guard itself blocks `pg_catalog`). Column-level grants, which would make the masking real, are the ui-1 decision.
4. **The guard fails closed** on constructs models commonly try (the table above); the hints say what to write instead.
5. **A sqlglot upgrade is a guard change.** sqlglot is pinned exactly in `pyproject.toml`, because its parser is the guard's view of a query. Bump it only together with a run of the guard's hostile-input tests ([Extending the MCP server](../advanced/extending-the-mcp-server.md)).
6. **The HTTP endpoint has no authentication.** Publish it on `127.0.0.1` only, never on `0.0.0.0`.
7. **The data's own caveats** (in `get_context`'s notes): one year of history, partial first and last months, refunds of recent orders still to come, every relative period anchored to `as_of_date`.
8. **Not part of the automated checks.** Connecting MCP Inspector and the `make up` / `make down` round trip are manual checks.

**Role limits.** The Postgres init script (`data-platform/postgres/init/00_roles.sh`) creates `sherpa_agent` with `CONNECTION LIMIT 20` and `temp_file_limit = 256MB`, but it runs only on an empty data volume. On an existing volume, apply the same limits once as the Postgres superuser:

```bash
docker compose exec postgres psql -U postgres -d sherpa -c "ALTER ROLE sherpa_agent CONNECTION LIMIT 20; ALTER ROLE sherpa_agent SET temp_file_limit = '256MB';"
```

## Tracing with Logfire

The server sends traces to Logfire when `LOGFIRE_TOKEN` is set, and exports nothing and prints nothing otherwise. Create a project at [logfire.pydantic.dev](https://logfire.pydantic.dev), create a write token in the project's settings, and put `LOGFIRE_TOKEN=<your write token>` in `.env` (the compose `mcp` service passes it through). The server calls `logfire.configure(send_to_logfire="if-token-present")` itself, so `LOGFIRE_SEND_TO_LOGFIRE` does not change it. To send the spans to another OpenTelemetry backend instead, run the server on the host with `OTEL_EXPORTER_OTLP_ENDPOINT` set (Logfire adds an OTLP exporter for it); the container's environment does not pass that variable.

What the trace holds:

- **One span per tool call**, `tools/call <tool>` (from the MCP SDK), with the tool name (`gen_ai.tool.name`) and, for a failed call, `error.type = tool_error`. It carries neither the arguments nor the error text.
- **A `sherpa.guard` span** per checked query: the tool, whether the query passed, the number of errors, the tables, the `execution_id` and, on a rejection, the rule. Never the SQL or the error texts, which can quote literals.
- **The psycopg statements on a plain connection**: the `set local` settings and `validate_sql`'s `explain (analyze false) <the SQL>`. Their span records the statement text (`db.statement`), so a literal in a validated query (an email address a user typed, say) is in the trace, and when that `explain` fails, the span also records Postgres's error message as an exception event, which can quote such a literal.
- Queries on the server-side cursor (`run_sql`, the `describe_table` samples and stored values, `search_values`, `get_context`) get no database span, so neither their text nor a class 22 error raised while fetching them (whose Postgres text can quote a stored value) is in the trace.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `[unavailable] dbt artifacts not found` | Run `make build-prod` and `make docs-prod`, as the message says |
| `[unreachable] the warehouse is unreachable` | Postgres is not up: `make up`, or `docker compose up -d --wait postgres` |
| The server exits at startup with `cannot connect to the warehouse` | Postgres is down, or `SHERPA_AGENT_PASSWORD` in `.env` does not match the role |
| The container is unhealthy or keeps restarting | `docker compose logs mcp`; the server exits when it cannot connect (as above) and `restart: unless-stopped` starts it again, so it stays unhealthy until the cause is fixed |
| `make mcp-dev` cannot start because the port is taken | The container already holds 8765; set another port on the command line: `SHERPA_MCP_PORT=8766 make mcp-dev` |
| A client gets HTTP 421 | The Host header is not an allowed name (`127.0.0.1`, `localhost`, `[::1]`, `mcp`); connect through one of those |
| A stdio client reports "connection closed" | The server exited or wrote to stdout. Run the same command in a terminal to see its stderr: usually a missing variable or `.env` (the working directory must be the repository root) |
| `uv` fails on Windows with "access denied" or `Failed to hardlink files` | A OneDrive folder; set `UV_LINK_MODE=copy` (in the client's `env` for stdio) |
| Certificate errors behind a TLS-intercepting proxy | For host runs, point `SSL_CERT_FILE` (uv) and `REQUESTS_CA_BUNDLE` (the Logfire exporter, which uses `requests`) at a CA bundle that includes the proxy's root certificate ([README](../../../README.md#windows-and-corporate-networks)) |
| `[timeout] the server is busy with other queries` | Two other queries are fetching their rows; retry in a few seconds |
