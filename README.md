# SQL Sherpa

**Self-service analytics on a dbt warehouse: ask in plain language, get an answer you can check.**

SQL Sherpa puts a text-to-SQL agent on top of a dbt data platform.
- **Business users** ask a question, such as "what was net revenue by channel last quarter?". The agent first shows how it will answer: which marts, which metric definition and which assumptions. You can correct that plan before anything runs. The agent then writes the SQL against the documented marts, runs it read-only, and returns the answer with the result, a chart when one helps, the SQL and its reasoning.
- **Analysts** use the same platform to browse the marts and their documentation, run their own read-only SQL, and follow the lineage from source to mart.

The answers can be trusted because of the platform underneath: one documented, tested, contract-enforced consumption layer, in which every metric is defined once. The agent, the web app and the analysts all read that same layer.

It is a learning project held to production standards: a realistic synthetic e-commerce business, dbt Core features used the way practitioners use them, and an agent where observability and evaluation come first. All data is synthetic.

> **Status:** data-platform-1 (Foundation), data-platform-2 (Core marts), data-platform-3 (Time), data-platform-4 (Marketing, governance and CI), agent-design-1 (MCP server), ui-1 (shell and Explorer), ui-2 (Lineage), ui-3 (column lineage) and ui-4 (Home) and agent-design-2 (gold set and evals) are built. agent-design-3 (baseline agent) is next. Each spec's milestones section lists the rest.

## How it works

```
 generator ──► Postgres raw_* ──► dbt: staging → intermediate → marts ──► MCP server ──► agent (Claude) ──┐
                                                    │                                                       ▼
                                                    └──────────── read-only SQL ───────────► web app: Home · Ask · Explorer · Lineage
```

1. **Generator.** A deterministic, day-by-day simulation of an online sports retailer writes four source systems into Postgres:
   - a Shopify-like shop (customers, catalog, orders, payments, promotions, returns and refunds)
   - a Segment-like event tracker
   - an ads platform
   - a CRM

   It injects the problems real data has: duplicate and late events, messy emails and country codes, test accounts, split payments and price changes.
2. **dbt.** Cleans and models that data into documented, contract-enforced marts (`fct_orders`, `dim_customers`, `fct_sessions`, `dim_campaigns`, `fct_ad_spend_daily`, `fct_leads`, …), with every metric defined once: net revenue, margin, conversion rate, ROAS, CAC.
3. **Agent.** A read-only MCP server exposes the marts, their dbt documentation and a guarded SQL tool. A PydanticAI agent running Claude on Amazon Bedrock plans an answer, takes your corrections, then answers. Every technique it uses has to raise its score on an execution-accuracy eval set.
4. **Web app.** It has four screens:
   - **Home:** headline numbers and data health
   - **Ask:** the agent, with its plan to review before it runs
   - **Explorer:** browse the marts and run read-only SQL
   - **Lineage:** the dbt graph

   The app connects with its own read-only role, and it works offline, except for the model.

## Stack

Tools arrive with the milestone that first needs them. Those marked *planned* are not installed yet.

| Layer | Tools |
|---|---|
| Data | PostgreSQL 16 (Docker), Python 3.12 + Faker |
| Transformation | dbt Core + dbt-postgres, dbt_utils, dbt_expectations; MetricFlow *planned* |
| Agent | the MCP Python SDK; PydanticAI and Claude on Amazon Bedrock *planned* |
| Web app | React + TypeScript + Vite, Mantine, CodeMirror, TanStack Query and Table, FastAPI, React Flow, ELK |
| Observability & evals | Pydantic Logfire (OpenTelemetry); the execution-accuracy eval runner (`sherpa_evals`, on the frozen `sherpa_eval` database) |
| Tooling | uv, ruff, mypy, sqlfluff, ESLint, pre-commit, GitHub Actions |

## Quickstart

You need Docker, [uv](https://docs.astral.sh/uv/) and GNU Make. On Windows, run `make` from Git Bash (see below).

```bash
cp .env.example .env    # then replace every change-me password
make up                 # start Postgres, the MCP server and the web app
make backfill           # simulate a year of data (about 9 minutes)
make build-prod         # build the warehouse that the agent and the web app read
```

Open <http://127.0.0.1:8090> for Home; the Explorer is at `/explorer` and Lineage at `/lineage`. The MCP server listens on `http://127.0.0.1:8765/mcp`.

From there:
- **Develop:**
  - Run `uv run pre-commit install` once.
  - `make build` builds your own dev schemas, `make slim` only the models you changed (after one `make build-prod`), and `make lint` and `make test` check your changes.
  - CI runs on every pull request: a tiny-data dbt build with warnings as errors, then lint, tests and the frontend tests ([CI guide](docs/guides/advanced/ci.md)).
  - For frontend work, run `cd ui/frontend && npm ci` once (Node 22+).
  - `CLAUDE.md` lists every Make target.
- **Add days:** `make advance DAYS=<n>`, then `make build-prod`.
- **One shared warehouse:** every checkout and git worktree uses the same Docker volume (`sherpa_pgdata`), so `make reset` anywhere resets it for all of them.

### Windows and corporate networks

- Run `make` from **Git Bash**. In PowerShell or `cmd`, GNU make runs the recipes with `cmd.exe`, and they need a POSIX shell.
- OneDrive folders can break uv's hardlinks (`Failed to hardlink files` or "access denied" errors). Set `UV_LINK_MODE=copy`.
- **Behind a TLS-intercepting proxy,** `uv` downloads and the dbt package install in `make build` fail with certificate verification errors.
  - Export the proxy's root certificate, together with the usual public roots, into a PEM bundle outside the repository.
  - Set `SSL_CERT_FILE` (uv) and `REQUESTS_CA_BUNDLE` (dbt's package hub client) to its path in `.env`. `.env.example` shows the lines.
- `localhost` can resolve to IPv6 on Windows while Docker publishes the port on IPv4, which is why `.env.example` uses `127.0.0.1`.

## Repository layout

Code is grouped by spec; the rest of `agent/` appears with its milestones.

```
.github/
  workflows/       ci.yml, the pull-request gate
data-platform/
  postgres/        database roles: roles.sql, and the init script that runs it on a new volume
  generator/       the synthetic-data generator (Python package, CLI and tests)
  dbt/             the dbt project: sources, seeds, staging, intermediate and mart models, macros and tests
  scripts/         helper scripts, such as the dbt docs-coverage check
  tests/           tests spanning the generator and dbt (seeds, reader access to the marts, scripts) and the roles' SQL (`postgres/`)
agent/
  mcp-server/      the read-only MCP server (Python package `sherpa_mcp`, its tests and Dockerfile)
  evals/           the gold set (`cases.yaml`), the eval runner (Python package `sherpa_evals`, its tests) and the committed run reports
ui/
  backend/         the web app's API (Python package `sherpa_ui`, its tests)
  frontend/        the web app (React + TypeScript + Vite, with Vitest and Playwright tests)
  parity/          the sort/filter case file that the backend and frontend tests both run
  Dockerfile       the `ui` image (builds the frontend, serves it with the API)
docs/
  superpowers/specs/   the three specs
  guides/              how-to guides, basic and advanced
```

## Specs

- [Data platform](docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md): the generator, the data model, the dbt layers and metric definitions
- [Agent](docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md): the MCP server, the plan-then-answer agent and its evals
- [Web app](docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md): Home, Ask, Explorer and Lineage

## Guides

### Basic: using it

- [Using the generator](docs/guides/basic/generator-cli.md): commands (`advance`, `backfill`, `start-date`), recipes, errors, and seeing the realism rules in the data
- [Building the warehouse](docs/guides/basic/building-the-warehouse.md): dev and prod, the layers, `make slim`, keeping it current (incremental models, price history, `--full-refresh`, `dbt retry`), running dbt directly, the docs, failing tests and the eval database
- [Querying the marts](docs/guides/basic/querying-the-marts.md): what each mart answers (sales, customers, products, web sessions, campaigns and ad spend, leads), which revenue to use, example queries, traps and fuzzy search
- [Using the MCP server](docs/guides/basic/using-the-mcp-server.md): starting it, connecting clients, the eight tools, PII, limitations and tracing
- [Running the evals](docs/guides/basic/running-the-evals.md): checking the gold set, scoring a variant, reading a report, where the records live and what the errors mean
- [Using Home](docs/guides/basic/using-home.md): the five tiles with their windows and change rules, the weekly trend, the data-problems box and footer, and what each "no data" or "unavailable" message means
- [Using the Explorer](docs/guides/basic/using-the-explorer.md): the catalog tree, the resizable panes, worksheets, running, sorting and filtering, and what it won't let you do
- [Using Lineage](docs/guides/basic/using-lineage.md): the three modes, the selector box, build status colours, expandable columns with their kinds, the column focus, the node panel (tabs, test dialogs) and copying a selector for `dbt`

### Advanced: extending it

- [Extending the source data model](docs/guides/advanced/extending-the-source-data-model.md): new columns, tables, enum values and realism rules, and keeping the generator deterministic
- [Adding or changing a mart](docs/guides/advanced/adding-or-changing-a-mart.md): new columns and marts, metric definitions, trigram indexes, custom tests, intermediate models and incremental models
- [Extending the MCP server](docs/guides/advanced/extending-the-mcp-server.md): a new tool, an allowed SQL function, guard hints, a sqlglot upgrade, few-shot examples, and new marts
- [CI: the pull-request gate](docs/guides/advanced/ci.md): what the gate runs and why, reproducing a failing step locally, and adding a check
- [Extending the gold set](docs/guides/advanced/extending-the-gold-set.md): the case schema and drafting rules, the review workflow, `gold_version` and `dataset_version`, the negative control, prices and adding an answerer
- [Extending the UI](docs/guides/advanced/extending-the-ui.md): a new endpoint, a new screen, a pane, a Home tile, the layout metrics, a test definition, a selector atom, a node status or a column kind, where the read-only rules live, and the three test layers
