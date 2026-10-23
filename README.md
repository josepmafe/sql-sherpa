# SQL Sherpa

**SQL Sherpa guides you to the right SQL.** It is an agentic layer that helps business stakeholders and data analysts query an e-commerce warehouse in plain language: it grounds your question in the warehouse's documented consumption layer, writes the SQL, checks it, runs it, and explains what it assumed.

It is a learning project, built to production standards: a realistic synthetic e-commerce business, a dbt project that uses dbt Core's features the way practitioners do, and a text-to-SQL agent where observability and evaluation come first.

> **Status:** design complete for both the data platform and the agent; data-platform-1 (Foundation) and data-platform-2 (Core marts) are done; data-platform-3 (Time) is next.

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

The target stack. Each tool is introduced in the milestone where it first does a job (see the roadmap); only the tools of the finished milestones (data-platform-1 and data-platform-2) are installed today.

| Layer | Tools |
|---|---|
| Data | PostgreSQL 16 (Docker), Python 3.12 + Faker (data-platform-1) |
| Transformation | dbt Core + dbt-postgres and dbt_utils (data-platform-1), dbt_expectations (data-platform-2), MetricFlow (data-platform-5) |
| Agent | PydanticAI, FastMCP, Claude on Amazon Bedrock (agent milestones) |
| Observability & evals | Pydantic Logfire (OpenTelemetry) and a small execution-accuracy eval runner (agent milestones) |
| Tooling | uv, ruff, sqlfluff, pre-commit (data-platform-1), GitHub Actions (data-platform-4) |

## Roadmap

| Milestone | What you get |
|---|---|
| **data-platform-1 · Foundation** | Docker Postgres, the generator, dbt sources + freshness, seeds, documented and tested staging models |
| **data-platform-2 · Core marts** | Order, promotion, customer and product marts; macros; custom, singular and dbt_expectations tests; contracts; grants and hooks |
| **data-platform-3 · Time** | Snapshots (price history), incremental models (microbatch, merge, delete+insert), sessionization, unit tests |
| **data-platform-4 · Governance & CI** | Marketing marts, groups and access, model versions, exposures, selectors, slim builds, GitHub Actions CI |
| **data-platform-5 · Semantic layer** | MetricFlow semantic models, metrics and saved queries |
| **agent-design-1 … 10 · Agent** | MCP server, eval harness, a baseline agent, then grounding, schema linking and self-correction, one at a time, measured |

## Quickstart

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/) and GNU Make.

```bash
uv run pre-commit install  # hooks enforce lint and format locally; there is no CI yet
cp .env.example .env     # then replace the change-me passwords with real values
make up                  # start Postgres
make backfill DAYS=365   # simulate a year of data into the raw schemas (about 3.5 minutes)
make deps                # install the dbt packages
make build               # dbt: seeds, staging, intermediate and mart models, and tests
make build-prod          # the same into the prod schemas, which the agent reads
make eval-db             # optional: the frozen evaluation database sherpa_eval (about 5 minutes or more)
make docs                # generate and serve the dbt docs until you press Ctrl+C
```

`make test` runs the tests except the `slow` and `warehouse` ones (markers in `CLAUDE.md`), and `make lint` runs ruff, mypy and sqlfluff.

To add days later, run `make generate DAYS=<n>`. Days added after the first run have no paid campaigns, promotions or ad spend, because the catalog is built once, on the first run.

### Windows and corporate networks

- OneDrive folders can break uv's hardlinks (`Failed to hardlink files` or "access denied" errors). Set `UV_LINK_MODE=copy`.
- Behind a TLS-intercepting proxy, `uv` downloads and `make deps` fail with certificate verification errors. Export the proxy's root certificate, together with the usual public roots, into a PEM bundle outside the repository and set `SSL_CERT_FILE` (uv) and `REQUESTS_CA_BUNDLE` (dbt's package hub client) to its path in `.env`; `.env.example` shows the lines.
- `localhost` can resolve to IPv6 on Windows while Docker publishes the port on IPv4, which is why `.env.example` uses `127.0.0.1`.

## Repository layout

```
generator/   the synthetic-data generator (Python package, CLI and tests)
dbt/         the dbt project: sources, seeds, staging, intermediate and mart models, macros and tests
docker/      Postgres init script (database roles)
scripts/     helper scripts, such as the dbt docs-coverage check
docs/        design specs, implementation plans and how-to guides
tests/       tests outside the generator package (dbt seeds, agent access to the marts, scripts)
```

## Design docs

- [Data platform spec (generator + dbt)](docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)
- [Agent spec (text-to-SQL agent)](docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md)
- [Plan — data-platform-1 Foundation](docs/superpowers/plans/2026-10-22-data-platform-1-foundation.md)
- [Plan — data-platform-2 Core marts](docs/superpowers/plans/2026-10-23-data-platform-2-core-marts.md)

## Guides

- [Using the generator](docs/guides/basic/generator-cli.md): commands, recipes, errors, and the raw data in `psql`
- [Building the warehouse](docs/guides/basic/building-the-warehouse.md): dbt on dev, prod and eval, the layers, hooks, selecting, previewing, docs and failing tests
- [Querying the marts](docs/guides/basic/querying-the-marts.md): what each mart answers, revenue definitions, copy-paste queries, traps, fuzzy search and the agent role
- [Extending the source data model](docs/guides/advanced/extending-the-source-data-model.md): new columns, tables, enum values and realism rules, end to end
- [Adding or changing a mart](docs/guides/advanced/adding-or-changing-a-mart.md): new columns, marts, metrics, trigram indexes, custom tests and intermediate models

All data is synthetic.
