# SQL Sherpa

**SQL Sherpa guides you to the right SQL.** It is an agentic layer that helps business stakeholders and data analysts query an e-commerce warehouse in plain language: it grounds your question in the warehouse's documented consumption layer, writes the SQL, checks it, runs it, and explains what it assumed.

It is a learning project, built to production standards: a realistic synthetic e-commerce business, a dbt project that uses dbt Core's features the way practitioners do, and a text-to-SQL agent where observability and evaluation come first.

> **Status:** design complete for both the data platform and the agent; data-platform-1 (Foundation), data-platform-2 (Core marts), agent-design-1 (MCP server) and ui-1 (shell and Explorer) are done; agent-design-2 (gold set and evals) and data-platform-3 (Time) are next.

---

## How it works

```
 Synthetic generator ──► Postgres raw_* schemas ──► dbt: staging → intermediate → marts ──► MCP server ──► Agent ──► you
 (day-by-day simulation)  (4 source systems)        (tested, documented, contracts)        (read-only)    (Claude)
```

1. **Generator.** A deterministic, day-by-day simulation of an online sports retailer writes four source systems into Postgres: a Shopify-like shop (customers, catalog, orders, payments, promotions, returns, refunds), a Segment-like event tracker, an ads platform, and a CRM. It deliberately injects the problems real data has: duplicate and late events, messy emails and country codes, test accounts, split payments, price changes.
2. **dbt.** Cleans and models that data into documented, contract-enforced marts (`fct_orders`, `dim_customers`, `fct_sessions`, …), with precisely defined metrics such as net revenue, margin, conversion rate, ROAS and CAC.
3. **Agent.** An MCP server exposes the marts, their dbt documentation and a read-only SQL tool. A PydanticAI agent running Claude on Amazon Bedrock uses them to answer questions. It returns the answer, its interpretation of your question, the SQL, and the result. Every technique the agent uses has to earn its place on an execution-accuracy eval set.

## Stack

The target stack. Each tool is introduced in the milestone where it first does a job (see the roadmap); only the tools of the finished milestones (data-platform-1, data-platform-2, agent-design-1 and ui-1) are installed today.

| Layer | Tools |
|---|---|
| Data | PostgreSQL 16 (Docker), Python 3.12 + Faker (data-platform-1) |
| Transformation | dbt Core + dbt-postgres and dbt_utils (data-platform-1), dbt_expectations (data-platform-2), MetricFlow (data-platform-5) |
| Agent | PydanticAI, the MCP Python SDK (`MCPServer`, agent-design-1), Claude on Amazon Bedrock (agent milestones) |
| UI | React + TypeScript + Vite, Mantine, CodeMirror, TanStack Query and Table; FastAPI backend (ui-1); React Flow (ui-2) |
| Observability & evals | Pydantic Logfire (OpenTelemetry; the MCP server, agent-design-1) and a small execution-accuracy eval runner (agent milestones) |
| Tooling | uv, ruff, sqlfluff, pre-commit (data-platform-1), GitHub Actions (data-platform-4) |

## Roadmap

| Milestone | What you get |
|---|---|
| **data-platform-1 · Foundation** | Docker Postgres, the generator, dbt sources + freshness, seeds, documented and tested staging models |
| **data-platform-2 · Core marts** | Order, promotion, customer and product marts; macros; custom, singular and dbt_expectations tests; contracts; grants and hooks |
| **data-platform-3 · Time** | Snapshots (price history), incremental models (microbatch, merge, delete+insert), sessionization, unit tests |
| **data-platform-4 · Governance & CI** | Marketing marts, groups and access, exposures for the UI and the agent, slim builds and `dbt clone`, GitHub Actions CI |
| **data-platform-5 · Semantic layer** | MetricFlow semantic models and metrics, used by the agent's MetricFlow path |
| **agent-design-1 … 9 · Agent** | MCP server, eval harness, a baseline agent that plans before it runs, then grounding, schema linking and self-correction, one at a time, measured |
| **ui-1 … 5 · Web app** | Explorer (browse marts, run read-only SQL), Lineage (dbt flowchart), Home (headline numbers and data health), Ask (review the agent's plan, then see the answer), and a one-command demo for anyone with Docker |

## Quickstart

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/) and GNU Make.

```bash
uv run pre-commit install  # hooks enforce lint and format locally; there is no CI yet
cp .env.example .env     # then replace the change-me passwords with real values
make up                  # start Postgres, the MCP server and the web app (all wait until healthy)
make backfill DAYS=365   # simulate a year of data into the raw schemas (about 3.5 minutes)
make deps                # install the dbt packages
make build               # dbt: seeds, staging, intermediate and mart models, and tests
make build-prod          # the same into the prod schemas, which the agent and the web app read; also writes catalog.json
make eval-db             # optional: the frozen evaluation database sherpa_eval (about 5 minutes or more)
make docs                # generate and serve the dev dbt docs until you press Ctrl+C
```

`make docs` and `make docs-prod` serve on the same port (8080), so stop one before starting the other.

Open <http://127.0.0.1:8090> for the web app's Explorer ([Using the Explorer](docs/guides/basic/using-the-explorer.md)); if something else holds port 8090, set `SHERPA_UI_PORT` (for example `SHERPA_UI_PORT=8091 make up`). `make ui-dev` runs it for development.

`make mcp-dev` runs the MCP server on the host instead of the container ([Using the MCP server](docs/guides/basic/using-the-mcp-server.md)). `make test` runs the tests except the `slow` and `warehouse` ones (markers in `CLAUDE.md`), and `make lint` runs ruff, mypy, sqlfluff, ESLint and `tsc`.

The database lives in one Docker volume, `sherpa_pgdata`, shared by every checkout and git worktree of the repository (the compose project is pinned to `sql-sherpa`). `make up` in a new worktree reuses the existing warehouse instead of starting an empty one (it also builds and starts the `mcp` and `ui` services). The flip side is that `make down` (which stops Postgres, `mcp` and `ui`) or `make reset` from any of them affects that one database.

To add days later, run `make generate DAYS=<n>`. Days added after the first run have no paid campaigns, promotions or ad spend, because the catalog is built once, on the first run.

### Windows and corporate networks

- Run `make` from **Git Bash**. In PowerShell or `cmd`, GNU make runs the recipes with `cmd.exe`, and they need a POSIX shell.
- OneDrive folders can break uv's hardlinks (`Failed to hardlink files` or "access denied" errors). Set `UV_LINK_MODE=copy`.
- Behind a TLS-intercepting proxy, `uv` downloads and `make deps` fail with certificate verification errors. Export the proxy's root certificate, together with the usual public roots, into a PEM bundle outside the repository and set `SSL_CERT_FILE` (uv) and `REQUESTS_CA_BUNDLE` (dbt's package hub client) to its path in `.env`; `.env.example` shows the lines.
- `localhost` can resolve to IPv6 on Windows while Docker publishes the port on IPv4, which is why `.env.example` uses `127.0.0.1`.

## Repository layout

Code is grouped by spec. The rest of `agent/` appears with its first milestones.

```
data-platform/
  postgres/init/   Postgres init script (database roles)
  generator/       the synthetic-data generator (Python package, CLI and tests)
  dbt/             the dbt project: sources, seeds, staging, intermediate and mart models, macros and tests
  scripts/         helper scripts, such as the dbt docs-coverage check
  tests/           tests spanning the generator and dbt (seeds, agent access to the marts, scripts)
agent/
  mcp-server/      the read-only MCP server (Python package `sherpa_mcp`, its tests and Dockerfile)
ui/
  backend/         the web app's API (Python package `sherpa_ui`, its tests)
  frontend/        the web app (React + TypeScript + Vite, with Vitest and Playwright tests)
  Dockerfile       the `ui` image (builds the frontend, serves it with the API)
docs/              design specs, implementation plans and how-to guides
```

## Design docs

- [Data platform spec (generator + dbt)](docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)
- [Agent spec (text-to-SQL agent)](docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md)
- [UI spec (web app)](docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md)
- [Plan — data-platform-1 Foundation](docs/superpowers/plans/2026-10-22-data-platform-1-foundation.md)
- [Plan — data-platform-2 Core marts](docs/superpowers/plans/2026-10-23-data-platform-2-core-marts.md)
- [Plan — agent-design-1 MCP server](docs/superpowers/plans/2026-10-24-agent-design-1-mcp-server.md)
- [Plan — ui-1 Shell and Explorer](docs/superpowers/plans/2026-10-24-ui-1-shell-and-explorer.md)

## Guides

- [Using the generator](docs/guides/basic/generator-cli.md): commands, recipes, errors, and the raw data in `psql`
- [Building the warehouse](docs/guides/basic/building-the-warehouse.md): dbt on dev, prod and eval, the layers, hooks, selecting, previewing, docs and failing tests
- [Querying the marts](docs/guides/basic/querying-the-marts.md): what each mart answers, revenue definitions, copy-paste queries, traps, fuzzy search and the agent role
- [Using the MCP server](docs/guides/basic/using-the-mcp-server.md): starting it, connecting clients, the eight tools with real calls, error codes and hints, result limits, masking, known limitations and tracing
- [Using the Explorer](docs/guides/basic/using-the-explorer.md): starting the web app, the catalog tree, worksheets, running, sorting and filtering, and what it will not let you do
- [Extending the source data model](docs/guides/advanced/extending-the-source-data-model.md): new columns, tables, enum values and realism rules, end to end
- [Adding or changing a mart](docs/guides/advanced/adding-or-changing-a-mart.md): new columns, marts, metrics, trigram indexes, custom tests and intermediate models
- [Extending the MCP server](docs/guides/advanced/extending-the-mcp-server.md): a new tool, an allowed SQL function, guard hints, a sqlglot upgrade, few-shot examples, and what to update when marts are added
- [Extending the UI](docs/guides/advanced/extending-the-ui.md): a new endpoint, a new screen, where the read-only rules live, and the three test layers

All data is synthetic.
