# Asking from the command line

`sherpa ask` puts a question to the baseline agent (`v0_baseline`): it plans how to answer, optionally lets you check that plan, runs the SQL read-only through the [MCP server](using-the-mcp-server.md), and prints the answer with its interpretation, the SQL and a footer of cost and timing. Rules: [agent spec](../../superpowers/specs/2026-10-22-sql-sherpa-agent-design.md) §5 and §6; to change the agent, [Extending the agent](../advanced/extending-the-agent.md).

## What you need

- The [Quickstart](../../../README.md#quickstart) done: `.env` filled in, `make up`, `make build-prod` (the agent reads the prod marts and `target-prod`).
- AWS credentials for Bedrock. The agent uses the profile `AWS_PROFILE` (default `ClaudeCode`) in `AWS_REGION` (default `eu-central-1`); the models are `SHERPA_MODEL_DEFAULT` and `SHERPA_MODEL_FAST` (`bedrock:<id>`, or `anthropic:<id>` with `ANTHROPIC_API_KEY`).
- By default the CLI starts the MCP server itself over stdio. For the running HTTP server set `MCP_TRANSPORT=http` (`MCP_HTTP_URL`, default `http://127.0.0.1:8765/mcp`).

## Asking

```bash
uv run sherpa ask "What was net revenue by country last quarter?"
```

| Flag | Does |
|---|---|
| `--variant <name>` | Runs that variant instead of `SHERPA_DEFAULT_VARIANT` (default `v0_baseline`) |
| `--confirm` | Shows the draft (interpretation and SQL) and asks `Run, correct or abort?` before anything runs |
| `--json` | Prints only the final outcome as JSON on stdout; progress, prompts and errors stay on stderr |

Progress lines go to the terminal as the agent works, one per step, such as `s2 schema: describe_table fct_orders`. The result then has these parts, in order (a hand-built outcome through the renderer, to show the layout):

```text
Spain made the most net revenue: EUR 10.

+-----------------------+
| country | net_revenue |
|---------+-------------|
| ES      | 10          |
| DE      | 5           |
+-----------------------+

Interpretation
s2
  metric: net_revenue (accepted_values)
  table: fct_orders (default)

SQL
select country, sum(net_revenue) as net_revenue
from marts.fct_orders
group by 1 order by 2 desc
4.2 s | model-a 1200 in / 345 out | EUR 0.0123 | trace: <trace id or link>
```

- **The answer and table:** the table is the query's own result (at most 20 rows shown, the rest counted); `Caveats` follow it when there are any.
- **The interpretation:** every choice the agent made (metric, filters, time range, tables, assumptions), grouped by the step that grounded it and tagged with how it was grounded. This is what to check first.
- **The SQL**, and `SQL revised` with the approved SQL when the answer step ran different SQL from the draft.
- **The footer:** latency, tokens per model, cost in EUR (`cost unknown` when the model has no entry in `agent/core/prices.yaml`) and the trace.

A question the agent cannot or should not guess prints `Cannot answer: <reason>` with the closest supported question, or a clarifying question with candidate readings. Ask again with the detail added.

## Checking the plan first

```bash
uv run sherpa ask --confirm "Best product last month"
```

The draft is shown, then `Run, correct or abort?`:

- `run` answers with exactly that SQL.
- `correct` asks `Correction:`; what you type is added as a free-text correction and the agent plans again, showing the new draft. An empty correction aborts.
- `abort` (or the end of input) stops without running anything.

## Exit codes

| Code | Means |
|---|---|
| 0 | answered |
| 1 | failed, or the command could not start (invalid configuration, unknown variant, the tool server did not start) |
| 3 | needs clarification |
| 4 | unanswerable |
| 5 | aborted at `--confirm` |

With `--json`, `sherpa ask "..." --json | jq .outcome` reads `answered`, `failed`, `needs_clarification`, `unanswerable` or, after an abort, `draft`.

## Traces in Jaeger

Traces are off until an endpoint is set. In `.env` (the commented lines of `.env.example`):

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
SHERPA_TRACE_URL_TEMPLATE=http://127.0.0.1:16686/trace/{trace_id}
```

```bash
docker compose --profile jaeger up -d jaeger    # UI at http://127.0.0.1:16686
```

The footer's `trace:` then prints a link that opens the run: a `sherpa.plan` or `sherpa.answer` span with the model calls and the MCP tool calls under it. Without the template the footer prints the bare trace id. Jaeger keeps traces in memory, so they are gone when it stops. For the compose `mcp` service set `SHERPA_CONTAINER_OTLP_ENDPOINT=http://jaeger:4318`. The agent's spans hold the prompts, the SQL, the result rows and the database's error text (the MCP server's spans hold query text only in the `explain` statement of `validate_sql`), so stop Jaeger when you are not tracing: `docker compose --profile jaeger stop jaeger`.

## When something goes wrong

A failed run prints `Failed: <reason>` and exits 1.

| Symptom | Cause and fix |
|---|---|
| `Failed: model provider unavailable: <code>` | Bedrock did not answer or was throttled, or the credentials failed; `<code>` is the error code (`ThrottlingException`) or the error class (`ModelTimeoutError`, `NoCredentialsError`, `TokenRetrievalError`, `ProfileNotFound`). botocore already retried throttling. Check `aws sts get-caller-identity --profile <AWS_PROFILE>` and the region; after an expired SSO login, log in again. The full error is on stderr |
| `Failed: model provider rejected the request: <status>` | Bedrock refused the request itself (`403` not authorised for the model, `400` an invalid field or model ID), which a retry will not fix. Read the full error on stderr, then check the model ID, `SHERPA_EFFORT_DEFAULT` and the account's access to the model |
| `Failed: tool calls kept failing` | A tool returned an error more often than its retry limit allows. The stderr line names the tool; ask again, or check the MCP server |
| `Failed: warehouse context unavailable: ...` | `get_context` could not read the dbt artifacts; the message is the server's. Run `make build-prod` |
| `Failed: tool server unavailable` | The MCP server went away or did not answer within 30 s while the question was running. Check the server (`make mcp-dev` or the `mcp` service, or Postgres) and ask again |
| `Failed: budget exceeded` | The question used more than 15 tool calls in an agent, 40 model requests or 400k tokens. Ask something narrower, or split the question |
| `Failed: sql attempts exhausted` | A fourth SQL attempt was needed after three failed (a failed `validate_sql` or `run_sql` counts), so the run ended; `Last error` and `Last SQL` show the last one. Name the table or column in the question, or use `--confirm` and correct the plan |
| `Failed: plan could not be validated` | The model kept returning a draft that broke a rule (SQL not validated, or not the validated SQL). Run again; if it repeats, make the question more specific |
| `Failed: answer could not be verified against results` | The answer text held a number the result does not support, so it was not shown. Run again, or read the table directly |
| `error: tool server unavailable: ...` (`Connection closed` over stdio, `Client failed to connect` over HTTP) | The MCP server could not start, so the command stopped before asking. Over stdio it gives up after 15 s without Postgres: check `.env` (`SHERPA_DB_*`, `SHERPA_AGENT_*`) and `docker compose up -d --wait postgres`. Over HTTP check that `make mcp-dev` or the `mcp` service is up |
| `error: invalid configuration: ...` | A required `.env` value is missing (`SHERPA_DB_HOST`, `SHERPA_DB_PORT`, `SHERPA_DB_NAME`, `SHERPA_AGENT_USER`, `SHERPA_AGENT_PASSWORD`); the agent reads `.env` from the directory you run it in, so run from the repository root |
| `error: unknown variant ...` | `--variant` is not a key of `VARIANTS`; the message lists the valid names |
| Footer says `cost unknown` | `agent/core/prices.yaml` has no entry for the model ID ([Extending the gold set](../advanced/extending-the-gold-set.md#prices)) |
