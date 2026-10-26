# Using the MCP server

`sherpa-mcp` is a read-only MCP server over the marts. It gives an agent eight tools to find tables, read their documentation, look up values, fetch metric definitions and examples, and check and run one SELECT. It needs the prod warehouse ([Building the warehouse](building-the-warehouse.md), `make build-prod`). Rules: [agent spec](../../superpowers/specs/2026-10-22-sql-sherpa-agent-design.md) §4; to change it, [Extending the MCP server](../advanced/extending-the-mcp-server.md).

## What it guarantees

A query can only read the marts, and nothing can be written:

- **The guard** (`sql_guard`) parses the SQL and rejects anything but one SELECT over `marts.<mart>` tables with allow-listed functions. It never rewrites the SQL.
- **The role**: the server connects as `sherpa_agent`, which can `SELECT` on the marts and nothing else.
- **The session**: read-only transactions, 5 s `statement_timeout`.
- **The result**: at most 100 rows, 10,000 characters per cell and 50,000 in all; `truncated` says when rows were left out.

## Starting it

The server reads `.env` (`SHERPA_DB_*`, `SHERPA_AGENT_*`, `SHERPA_MCP_*`, `MCP_TRANSPORT`) and the dbt artifacts in `data-platform/dbt/target-prod/`, which `make build-prod` writes.

- **In compose:** `make up` starts Postgres and the `mcp` service. It publishes `127.0.0.1:8765` (host port from `SHERPA_MCP_PORT`), mounts `data-platform/dbt` for the artifacts, and has a 1 GB memory limit with `restart: unless-stopped`. After a code change: `docker compose up -d --build --wait --no-deps mcp`.
- **On the host over HTTP:** `make mcp-dev` serves `http://127.0.0.1:8765/mcp` from your checkout. If the container holds the port: `SHERPA_MCP_PORT=8766 make mcp-dev`.
- **Over stdio, for a client that starts it:** `MCP_TRANSPORT=stdio` (the default) speaks JSON-RPC on stdin and stdout, with logs on stderr. An `mcpServers` block:

```json
{"mcpServers": {"sherpa": {
  "command": "uv",
  "args": ["run", "--directory", "/path/to/sql-sherpa", "sherpa-mcp"],
  "env": {"MCP_TRANSPORT": "stdio"}}}}
```

For Claude Code: `claude mcp add --transport http sherpa http://127.0.0.1:8765/mcp`. The HTTP endpoint has no authentication: keep the port on `127.0.0.1`.

## Connecting MCP Inspector

With the server running, start `npx @modelcontextprotocol/inspector`, set the transport to **Streamable HTTP** and the URL to `http://127.0.0.1:8765/mcp`, connect, and open **Tools** > **List Tools**. For stdio, choose **STDIO**, command `uv`, arguments `run --directory <repository root> sherpa-mcp`.

## Calling it from Python

```python
import asyncio
from mcp import Client


async def main() -> None:
    async with Client("http://127.0.0.1:8765/mcp") as client:
        result = await client.call_tool("get_context", {})
        print(result.content[0].text)


asyncio.run(main())
```

Tools return JSON text; a failed call has `result.is_error` set and the text `Error executing tool <name>: <message>`.

## The eight tools

- **`get_context {}`**: the as-of date (the latest order date; use it instead of today), what the warehouse covers (one line per domain: sales, customers, products, web sessions, calendar) and does not (`does_not_cover`), and notes on the data, such as that sessions are web only, so `conversion_rate` leaves app orders out.
- **`list_tables {}`**: every mart with description, domain, grain and primary key.
- **`describe_table {"table"}`**: grain, primary key, and per column the type, description, PII flag, accepted values, `distinct_values` (the stored values of a short text column) and synonyms, plus three masked sample rows.
- **`search_values {"table", "column", "term", "limit"}`**: how a value is stored in a text column (`limit` 1 to 50, default 10). Use it before filtering on a name the user typed. PII columns are refused.
- **`get_metric_definitions {}`**: the official definitions (`aov`, `cogs`, `discount_amount`, `gross_margin`, `gross_revenue`, `net_revenue`, `refund_rate`, `refunded_revenue`).
- **`get_examples {"question", "k"}`**: up to `k` (1 to 10, default 3) worked examples with question, SQL, notes and `score`. The score only orders examples; it does not say whether the warehouse can answer the question.
- **`validate_sql {"sql"}`**: the guard, then a Postgres `EXPLAIN` without execution. A bad query is a normal result (`ok: false` plus `errors`); a warning flags a query that reads the real clock (`now()`).
- **`run_sql {"sql"}`**: runs one SELECT exactly as written (a trailing `;` is dropped). `execution_id` is the first 16 characters of the SHA-256 of the executed SQL.

```
run_sql {"sql": "select current_status, count(*) as orders from marts.fct_orders group by 1 order by 1;"}
{"execution_id": "51d5a285dca6768b", "columns": ["current_status", "orders"], "column_types": ["text", "int8"],
 "rows": [["cancelled", 411], ["delivered", 7316], ["paid", 33], ["shipped", 56]],
 "returned_rows": 4, "truncated": false, "elapsed_ms": 3}

describe_table {"table": "dim_customers"}
{"name": "dim_customers", "grain": "one row per customer", "primary_key": ["customer_id"],
 "columns": [{"name": "email", "data_type": "text", "is_pii": true, ...}, ...], "sample_rows": {...}}
```

Money comes back as strings (`"6.99"`), dates as ISO text; numbers depend on the seed.

## When something goes wrong

Errors start with a code: `[guard:<rule>]` (the hint after the `;` says what to write instead), `[sql]`, `[timeout]`, `[unreachable]` or `[unavailable]`. Postgres data errors (class 22) name the exception, not the offending value, which could be PII.

| Symptom | Fix |
|---|---|
| `[unavailable] dbt artifacts not found` | Run `make build-prod`; the server notices the new files, no restart |
| `[unreachable]`, or the server exits with `cannot connect` | Postgres is down (`make up`; also check `docker compose logs mcp`), or `SHERPA_AGENT_PASSWORD` in `.env` does not match the role |
| `[guard:...]` rejection | Follow the hint: qualify tables as `marts.<table>`, name columns instead of `select c`, use `IN` instead of `= ANY(ARRAY[...])`, `LIKE` instead of `~`, `marts.dim_date` instead of `generate_series` |
| `[timeout]` | The query ran over 5 s, or two other queries held the slots for 10 s; aggregate or filter more narrowly, or retry |
| `make mcp-dev`: port taken | The container holds 8765: `SHERPA_MCP_PORT=8766 make mcp-dev` |
| A client gets HTTP 421 | The Host header must be `127.0.0.1`, `localhost`, `[::1]` or `mcp` |
| A stdio client reports "connection closed" | The server exited or wrote to stdout; run the command in a terminal to read stderr (usually a missing `.env`; run from the repository root) |
| `uv` fails on Windows with "access denied" or `Failed to hardlink files`, or TLS errors | OneDrive folder: set `UV_LINK_MODE=copy` (in the client's `env` for stdio). Proxy: see the [README](../../../README.md#windows-and-corporate-networks) |

## PII masking

`run_sql` and the `describe_table` samples replace an output column with `***` when its **name** is a PII column in any mart (`email`, `first_name`, `last_name`; `meta.pii` in the dbt YAML). `NULL` stays `NULL`: `select customer_id, email from marts.dim_customers` returns `[1, "***"]`.

Name-based masking is an accepted limitation. It does not catch an alias (`email as e`), an expression (`lower(email)`), an aggregate (`string_agg(email, ',')`), a set operation that renames the column, or a `LIKE`/count oracle.

## Known limitations

- **Result size inside Postgres.** Postgres cannot cap one value while building it, so an adversarial `string_agg` or `replace(lpad(...))` can build a large value within 5 s. It is bounded by the guard's limits, the cell and result caps, two concurrent fetches and the container's 1 GB limit with restart. A server started over stdio has no memory limit.
- **PII in `pg_stats`.** The `sherpa_agent` role can read PII values there, outside the guard.
- **A sqlglot upgrade is a guard change.** It is pinned exactly; bump it only with a run of the guard's hostile-input tests.
- **The data's own caveats** are in `get_context`'s notes.

## Tracing with Logfire

The server sends traces to Logfire when `LOGFIRE_TOKEN` is set in `.env`, and exports nothing otherwise. Get a write token at [logfire.pydantic.dev](https://logfire.pydantic.dev). A trace holds one `tools/call <tool>` span per call (never the arguments) and a `sherpa.guard` span per checked query (outcome, tables, `execution_id`, rule; never the SQL). `validate_sql`'s `EXPLAIN` records its text, so a literal in a validated query is in the trace.
