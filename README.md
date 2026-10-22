# SQL Sherpa

**SQL Sherpa guides you to the right SQL.** It is an agentic layer that helps business stakeholders and data analysts query an e-commerce warehouse in plain language: it grounds your question in the warehouse's documented consumption layer, writes the SQL, checks it, runs it, and explains what it assumed.

It is a learning project, built to production standards: a realistic synthetic e-commerce business, a dbt project that uses dbt Core's features the way practitioners do, and a text-to-SQL agent where observability and evaluation come first.

> **Status:** design complete for both the data platform and the agent; implementation starts with milestone data-platform-1 (Foundation).

---

## How it works

```
 Synthetic generator ──► Postgres raw_* schemas ──► dbt: staging → intermediate → marts ──► MCP server ──► Agent ──► you
 (day-by-day simulation)  (4 source systems)        (tested, documented, contracts)        (read-only)    (Claude)
```

1. **Generator.** A deterministic, day-by-day simulation of an online sports retailer writes four source systems into Postgres: a Shopify-like shop (customers, catalog, orders, payments, promotions, returns, refunds), a Segment-like event tracker, an ads platform, and a CRM. It deliberately injects the problems real data has: duplicate and late events, messy emails and country codes, test accounts, split payments, price changes.
2. **dbt.** Cleans and models that data into documented, contract-enforced marts (`fct_orders`, `dim_customers`, `fct_sessions`, `obt_order_items`, …), with precisely defined metrics such as net revenue, margin, conversion rate, ROAS and CAC.
3. **Agent.** An MCP server exposes the marts, their dbt documentation and a read-only SQL tool. A PydanticAI agent running Claude on Amazon Bedrock uses them to answer questions. It returns the answer, its interpretation of your question, the SQL, and the result. Every technique the agent uses has to earn its place on an execution-accuracy eval set.

## Stack

| Layer | Tools |
|---|---|
| Data | PostgreSQL 16 (Docker), Python 3.12 + Faker |
| Transformation | dbt Core + dbt-postgres, dbt_utils, dbt_expectations, MetricFlow |
| Agent | PydanticAI, FastMCP, Claude on Amazon Bedrock |
| Observability & evals | Pydantic Logfire (OpenTelemetry), a small execution-accuracy eval runner |
| Tooling | uv, ruff, sqlfluff, pre-commit, GitHub Actions |

## Roadmap

| Milestone | What you get |
|---|---|
| **data-platform-1 · Foundation** | Docker Postgres, the generator, dbt sources + freshness, seeds, documented and tested staging models |
| **data-platform-2 · Core marts** | Order, customer and product marts; macros; custom, singular and dbt_expectations tests; contracts; grants and hooks |
| **data-platform-3 · Time** | Snapshots (price history), incremental models (microbatch, merge, delete+insert), sessionization, unit tests |
| **data-platform-4 · Governance & CI** | Marketing marts, groups and access, model versions, exposures, selectors, slim builds, GitHub Actions CI |
| **data-platform-5 · Semantic layer** | MetricFlow semantic models, metrics and saved queries |
| **agent-design-1 … 9 · Agent** | MCP server, eval harness, a baseline agent, then grounding, schema linking and self-correction, one at a time, measured |

## Repository layout

```
docs/        design specs and implementation plans
```

## Design docs

- [Data platform spec (generator + dbt)](docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)
- [Agent spec (text-to-SQL agent)](docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md)
- [Plan — data-platform-1 Foundation](docs/superpowers/plans/2026-10-22-data-platform-1-foundation.md)

All data is synthetic.
