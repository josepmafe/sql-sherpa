# CLAUDE.md

SQL Sherpa is a **toy project held to production standards**: a synthetic e-commerce warehouse (generator + dbt on Postgres) and a text-to-SQL agent on top of it. The owner is learning dbt (new to them) and building their first text-to-SQL agent (they already know agentic systems). Explain dbt concepts when they come up; don't over-explain generic agent plumbing.

## Source of truth

- **Data platform spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`. The data model, metric definitions, realism rules and dbt feature roadmap live there.
- **Spec compliance:** the specs win over the plans. If a plan conflicts with a spec, follow the spec and say so. Deviate from a spec only when the owner explicitly asks, and then update the spec once the task is done.
- **Agent spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`. Relies only on the interface in the data platform spec §11.
- **UI spec:** `docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md`. The web app (Home, Ask, Explorer, Lineage). Relies on the data platform spec §11 and the agent spec's `plan`/`answer` contract (§5.1).
- **Milestones and plans:** the data platform is built in milestones `data-platform-1` … `data-platform-5`, the agent in `agent-design-1` … `agent-design-9`, and the UI in `ui-1` … `ui-6` (each spec's milestones section). Each milestone has one plan in `docs/superpowers/plans/`, named `<date>-data-platform-<n>-<name>.md`, `<date>-agent-design-<n>-<name>.md` or `<date>-ui-<n>-<name>.md`. A plan is written **just before** its milestone is built, never all upfront. data-platform-1 (`2026-10-22-data-platform-1-foundation.md`), data-platform-2 (`2026-10-23-data-platform-2-core-marts.md`), data-platform-3 (`2026-10-25-data-platform-3-time.md`), agent-design-1 (`2026-10-24-agent-design-1-mcp-server.md`), ui-1 (`2026-10-24-ui-1-shell-and-explorer.md`), ui-2 (`2026-10-26-ui-2-lineage.md`) and ui-3 (`2026-10-27-ui-3-column-lineage.md`) are done. Current plan: agent-design-2 (gold set and evals), to be planned.
- **Sequencing:**
  1. data-platform-1 → data-platform-2 (defines the agent-facing interface).
  2. agent-design-1 to agent-design-3 alongside data-platform-3; ui-1 to ui-4 once agent-design-1 exists (ui-1 to ui-3 are built); ui-5 (Ask) after agent-design-3; ui-6 (one-command demo) after ui-5.
  3. data-platform-4 → agent-design-4 (gold set batch 2) → agent-design-5 to agent-design-8; data-platform-5 → agent-design-9.
- **Every plan ends with a documentation task**, and writing a plan includes writing it. The task does two things:
  - **Checks `CLAUDE.md` and `README.md`** against what the milestone changed (commands, layout, status, conventions) and updates them.
  - **Writes or updates how-to guides** in `docs/guides/`:
    - `basic/`: how to *use* what the milestone built, with easy and complete, copy-pasteable examples.
    - `advanced/`: how to *extend or modify* it, i.e. which files to touch, in which order, and which tests prove the change.

    Every milestone that adds something a person runs or extends gets at least one guide of each kind, or updates existing guides. Guides follow the current-state rule like every other doc.
  - **Guides stay short:**
    - Length: a basic guide under about 150 lines and an advanced one under about 250.
    - Output: show it only where reading it is the point (a failure to diagnose, a result to compare), cut to about 10 lines.
    - Links: link the README Quickstart, the spec section or another guide instead of repeating setup, rules or definitions.
    - Errors: each guide has one symptom → fix table.
    - New guides: a milestone extends the guide for the thing it changes; a new guide is only for a new thing a person runs or extends.
- **The last task, after the documentation task, is a panel review** of the whole branch. The plan names the panel for what the milestone builds and gives each reviewer a focus:
  - a data engineer for dbt and the generator
  - a software engineer for Python and the Makefile
  - a frontend engineer for the UI
  - a security engineer when roles, SQL access, PII or the network surface change
  - an AI engineer for the agent, its tools and evals
  - an analytics domain expert for metric and data semantics
  - always a devil's advocate

  The controller session runs the task itself (it dispatches the reviewers):
  1. **Reviewers:** it dispatches every reviewer in parallel on `opus`. Each is a fresh subagent with its focus, `git diff main...HEAD`, the plan and the relevant spec sections. A reviewer reads the code and runs read-only commands, and makes no edits. It returns findings as `Critical | Important | Minor` · `file:line` · what is wrong · why it matters · the fix.
  2. **Devil's advocate:** then it dispatches the devil's advocate on `opus`, with the diff and every finding. It challenges findings that are wrong or would over-engineer, and adds the failure modes the panel missed.
  3. **Triage:** it triages every finding in one table as fix, defer or reject, with a one-line reason. A rejection cites the spec or a test. Deferring a Critical or Important finding needs the owner's OK; an agreed deferral becomes a spec later idea or a line in the next plan.
  4. **Fixes:** each fix starts with a test that reproduces the problem, then goes through the usual implement → review loop. A fix that changes a design decision also updates the spec and the guides.

  The panel is the plan's final whole-branch review. Its report isn't committed: the triage table goes in the PR description.
- **Every plan task names its models.** Each task has a `**Model:**` line naming its implementer and its reviewer:
  - `haiku` for mechanical work (docs-only edits, renames, config).
  - `sonnet` by default.
  - `opus` only for genuinely hard design or concurrency work.

  Fix rounds 4–5 go one tier up. Plan writing and the panel review run on `opus`. Implementation sessions run on Sonnet (`/model sonnet`). `.claude/settings.json` sets `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`, so a dispatch that names no model gets Sonnet rather than the session's model; an explicit `model` on the dispatch still wins.
- **Docs describe the current state only.** `CLAUDE.md`, the README, the specs and the plans say what *is*, not what used to be. When something changes, rewrite the affected text in place. Don't add "previously…", "no longer…", "was replaced by…" or notes about discarded approaches; git history is the archive. Anything removed was removed on purpose, so don't carry it over.

## Principles

- **Every dbt feature has a real purpose.** Features are introduced in the milestone where they first do a job (the data platform spec §8). Don't pull later-milestone features forward without asking.
- **Keep the generator simple.** It is a day-by-day simulation with a fixed list of realism rules R1–R12 (the data platform spec §4.4). Don't add rules or knobs beyond the spec.
- **The agent is built incrementally and measured.** Start from a single-agent baseline and add one technique at a time. Each technique has to improve the eval scores. Keep the eval code small and boring.
- **Observability and evals are not afterthoughts.** Tracing comes with the first agent code, and the eval harness comes before any pipeline complexity.

## Commands

A Make target exists only for a multi-step workflow, a command with non-obvious flags, or a daily dev loop; anything else is a documented plain command. Don't add aliases. A helper that other targets call is a private target: its name starts with `_` (`_roles`, `_docs-check`, `_lockfile-check`, `_ui-api-dev`, `_ui-web-dev`), it sits at the bottom of the Makefile, and docs don't list it as a command.

- **Stack:**
  - `make up` / `make down` start / stop every compose service (Postgres, `mcp`, `ui` at `http://127.0.0.1:8090`). `make up` builds the images, waits until all are healthy, then brings the database roles up to date with `data-platform/postgres/roles.sql` (idempotent: it creates missing roles and resets passwords and settings from `.env`).
  - For the database alone: `docker compose up -d --wait postgres`.
- **Data:**
  - `make advance DAYS=<n>` simulates `n` more days (`DAYS` is required), then snapshots the catalog.
  - `make backfill` simulates `DAYS` days (default 365) ending yesterday, in weekly chunks with a snapshot after each; it refuses to run on top of an existing simulation.
  - `make reset` drops the dbt schemas (`dev_*`, the prod ones, `snapshots`, `test_failures`, `dev_test_failures`, `ops`) and the raw schemas, then recreates the raw schemas empty.
  - The simulated range and row counts: `uv run sherpa-generate status`.
- **dbt:**
  - `make build` checks source freshness, then runs `dbt build` (seeds, unit tests and the incremental models included) on dev. It reads the simulation's first day (`uv run sherpa-generate start-date`) into `SHERPA_SIMULATION_START_DATE`, the `begin` of the event model. When a simulated day older than 7 days before today that no earlier build loaded exists (for example after `make advance DAYS=n` with `n` above 7), `assert_every_source_event_is_staged` fails the build until you run `dbt build --select stg_events__events --event-time-start <first missing day> --event-time-end <day after the last missing day>` (the end is exclusive) from `data-platform/dbt/` (the building guide shows it).
  - `make build-prod` does the same on prod, then `dbt docs generate`, with artifacts in `target-prod`.
  - Both install the dbt packages first whenever `packages.yml` or `package-lock.yml` changed.
  - `make docs` generates and serves the dev docs.
  - `make eval-db` drops and rebuilds the frozen `sherpa_eval` database (seed 42, 2025-01-01, 365 days; artifacts in `target-eval`).
  - The read grants are re-applied by every build's `on-run-end` hook. Re-apply them without a build with `dbt run-operation grant_read_access --target prod --target-path target-prod`.
- **Checks:**
  - `make lint` runs ruff (check and format), mypy, sqlfluff, the docs-coverage check (every model and column under `data-platform/dbt/models` described), ESLint and `tsc` in `ui/frontend`, the npm lockfile's registry check, and a diff of the committed API client against the backend's schema.
  - `make test` runs pytest with `.env` loaded (`slow` and `warehouse` excluded; the integration tests need Postgres). The frontend's unit tests: `cd ui/frontend && npm test` (Vitest).
  - `make e2e` runs Playwright against the compose stack (`make up`, `make build-prod`; `npx playwright install chromium` once). Its freshness spec skips itself because it adds a simulated day and rebuilds prod in the shared warehouse. Run it on purpose with `cd ui/frontend && SHERPA_E2E_FRESHNESS=1 npx playwright test freshness`.
- **Dev loops:**
  - `make mcp-dev` runs the MCP server on the host over HTTP (`http://127.0.0.1:8765/mcp`).
  - `make ui-dev` runs the API with reload plus the Vite dev server, which proxies `/api` to it, on `SHERPA_UI_PORT` (default 8090). Stop the compose `ui` service first, or pass `SHERPA_UI_PORT=8092`. It needs `cd ui/frontend && npm ci` once (Node 22+), like `make lint` and `make e2e`.
  - `make ui-client` regenerates `ui/frontend/src/api/schema.d.ts` from the backend's OpenAPI schema; commit it after any API change.

## Conventions

- **Repository layout, grouped by spec:**
  - `data-platform/`: `postgres/` (`init/` runs once on a new volume; `roles.sql` and `large_objects.sql` hold the SQL that it and `make up` share), `generator/`, `dbt/`, `scripts/` (`backfill.py` for `make backfill`, `check_docs_coverage.py`), and `tests/` for tests that span the generator and dbt (`dbt/`, `scripts/`) and the SQL tests of `postgres/`.
  - `agent/mcp-server/` (the `sherpa_mcp` package) exists; `ui/` has `backend/` (the `sherpa_ui` package), `frontend/` (React + TypeScript + Vite; `src/`, `tests/` for Vitest and `e2e/` for Playwright), `parity/` (`sort_filter_cases.json`, the sort/filter case file that pytest and Vitest both run) and the `Dockerfile`; `agent/core/` and `agent/evals/` appear with their first milestones.
  - Every Python component is `<component>/src/<package>/` plus its own `tests/`.
  - A component's Dockerfile sits next to its code, and the root `docker-compose.yml` wires the services together.
  - Commands run from the repository root; dbt runs from `data-platform/dbt/` (the Makefile does the `cd`).
- Python 3.12, uv, single root `pyproject.toml` (dependency groups `generator`, `dbt`, `mcp`, `ui`, `dev`); the frontend has its own `ui/frontend/package.json` (npm). `sqlglot` is pinned exactly: its parser is the SQL guard's view of a query, so a bump is a guard change and a column-lineage change (rerun the guard's hostile-input tests and the column-lineage tests). Lint/format with ruff; SQL with sqlfluff (dbt templater, Postgres dialect).
- dbt naming: `stg_<source>__<entity>`, `int_<entity>__<verb>`, `fct_` / `dim_`. YAML per directory: `_<dir>__models.yml`, `_<source>__sources.yml`. Every model and column gets a description.
- Schemas: `dev_`-prefixed on the `dev` target, verbatim on `prod`, snapshots always in `snapshots`. Prod dbt commands use `--target-path target-prod`.
- Generator determinism: never use Python's built-in `hash()` (use `stable_int` / `DeterministicRandom`). The generator assigns its own primary keys. Nothing may be timestamped after yesterday (UTC).
- Time-relative columns use `as_of_date`, never `now()`.
- dbt tests: singular tests are plain `SELECT`s of failing rows in `data-platform/dbt/tests/singular/`; unit tests sit in the model's YAML, with test names of at most 63 characters (Postgres' identifier limit). Every incremental model sets `on_schema_change: fail`.
- Money is `numeric(12,2)` EUR (Python `Decimal`, `ROUND_HALF_UP`). Timestamps are UTC.
- Secrets live only in `.env` (gitignored). `profiles.yml` reads everything via `env_var()`.

## Working Style

- **Think before coding.** State your assumptions. When something is ambiguous, name the interpretations instead of silently picking one. Push back when a simpler approach exists, and stop and ask when confused.
- **Simplicity first.** Write the minimum code that solves the problem: no speculative features, no abstractions for single-use code, no configurability nobody asked for, no error handling for impossible cases. If 200 lines could be 50, rewrite it.
- **Surgical changes.** Touch only what the task needs. Match the existing style, and don't refactor or reformat adjacent code. Mention unrelated dead code rather than deleting it, and remove only what *your* change made unused.
- **Build incrementally: a commit adds only what that commit uses.** A file, dependency, dependency group, config section, Makefile target, `.env` variable or `.gitignore` entry appears in the commit where something first needs it. Examples: `make up` arrives with `docker-compose.yml`; dbt targets with the dbt project; `mypy` config with the first typed module. There are no scaffold commits, placeholder files, empty packages, stub targets or "TODO later" config. Every commit leaves the repo working, with its tests and lint passing.
- **Goal-driven.** Turn tasks into checks you can verify ("write a test that reproduces it, then make it pass"). For multi-step work, state a short plan as `1. step → verify: check` and loop until every check passes.
- **Finishing a task:** use `superpowers:finishing-a-development-branch`, then check whether a spec needs updating to reflect design decisions made during implementation.

## Python Guidelines

### Structure
- Keep **pure logic separate from I/O**, so most code is testable without a database or a model:
  - generator: `records/`, `RealismRules`, `SimulationCalendar`, `DeterministicRandom` and `OrderPricing` are pure; `database/`, `StoreSnapshotLoader` and `Simulator` do the I/O. `OrderPricing` lives in `behaviour/order_lifecycle.py` and `GeneratorError` in `simulation/simulator.py`
  - MCP server: `sql_guard`, `masking`, the `examples` ranking and the `catalog` parsing are pure; `tools`, `database` and the file reads of the `catalog` loader and of `ExampleIndex.from_yaml` do the I/O
  - UI backend: `readonly/sort_filter`, the `catalog_service` mapping, `lineage/manifest_graph`, `lineage/selector`, `lineage/node_status` and `lineage/column_lineage` are pure; `readonly/query_runner`, `build/build_status`, `lineage/manifest_loader`, `lineage/node_sql`, `lineage/column_lineage_loader`, `lineage/lineage_service` and the `api/` routers do the I/O
  - agent: `guards` and `outcomes` are pure; variants orchestrate
- One package per component (`sherpa_generator`, `sherpa_mcp`, `sherpa_ui`, `sherpa_agent`), each with its own `settings.py` (pydantic-settings, `frozen=True`, env prefix `SHERPA_`). Read configuration only through it; never call `os.environ` elsewhere.
- Module-level `UPPER_CASE` constants for every tunable value (rates, limits, thresholds), with the spec section in a comment where it comes from the spec. No magic numbers inside logic.
- **Prefer classes over collections of functions.** Behaviour that shares dependencies, configuration or state, or forms one responsibility, lives in a class whose constructor takes those dependencies. Examples: `SqlGuard(catalog)`, `Simulator(db, seed=…, scale=…, today=…)`, `ResultComparator`, one class per variant. Two exceptions stay module-level functions: tiny stateless utilities with no shared context (e.g. `stable_int`), and entry points that frameworks require as functions (Typer commands, MCP tool handlers, which delegate to a class). Don't write namespace-only classes made of `@staticmethod`s.
- **Top-down ordering (stepdown rule).** A file reads from the public API to the details:
  - **Module:** imports, constants and type aliases (they must exist at import time) → public classes and functions → private (`_`-prefixed) helpers at the bottom.
  - **Class:** class attributes, `__init__` and other dunders → public methods and properties → private methods.
  - A caller comes before the helpers it calls, so you can stop reading once you've seen enough.

### Types and data
- Type hints on every function, including tests' helpers; mypy runs on the source packages with `disallow_untyped_defs`. Use modern syntax (`X | None`, `list[str]`) and `from __future__ import annotations` where forward references need it.
- Imports used only for typing go under `if TYPE_CHECKING:` (ruff `TC`). Exception: annotations that Pydantic or Typer resolve at runtime.
- **Frozen Pydantic models for structured data:** raw source records, config, MCP tool inputs/outputs, `Outcome`, eval cases. No positional tuples and no bare `dict`s cross a module boundary; nested payloads such as JSON columns get their own model.
- `StrEnum` with `auto()` for enumerations that are part of the domain; `Literal[...]` for small closed parameter sets.
- Money is `Decimal` end to end, never `float`.

### Control flow and errors
- Use `match`/`case` instead of if-chains for dispatch (variant registry, outcome rendering, factories).
- Each package defines its own error type (`GeneratorError`, `GuardError`, …) for user-facing failures. Catch narrow exceptions, never bare `except:` or a silent `except Exception`, and re-raise with `raise … from exc`.
- Don't hand-roll retries around libraries that already retry (botocore for Bedrock, PydanticAI for tool and output retries). Configure their limits explicitly instead.
- Async code (MCP server, agent, eval runner) must not make blocking calls (ruff `ASYNC`). The generator is synchronous on purpose.

### Logging and output
- Stdlib `logging` only: `logger = logging.getLogger(__name__)` per module, with `%`-style arguments (`logger.info("wrote %d rows", n)`, not f-strings). No `print()` outside CLI output (Typer/Rich) and helper scripts (`data-platform/scripts/`).
- **The MCP server never writes to stdout** (it carries the JSON-RPC stream): logs go to stderr, and Logfire runs with `console=False`.

### Formatting
- ruff format; line length 120 (prompt strings in agent modules may exceed it).
- Leave a blank line after a `with`, `if` or `try/except` block before the next statement, but not before the block.
- Docstrings only where the *why* isn't obvious from the name and signature; keep them to a few lines. Comments explain intent and constraints, not what the code does.

### Tests
- Each component's `tests/` mirrors its package layout. For a class: one test class per target class, with one or more test methods per method under test. For a function: top-level test functions.
- Arrange, act and assert are separated by blank lines. Assertions use the spec's exact values.
- **No shallow tests.** Don't test what is obvious or what a third-party package already guarantees: config defaults, enum values, that a dataclass stores its fields, that Pydantic rejects a wrong type, that psycopg can insert a row. Test *our* logic and *our* use of the library (e.g. that we pass the right `UsageLimits`, not that PydanticAI enforces them).
- **Edge cases and the unhappy path, not just the happy path.** For every unit, ask what happens on:
  - empty input
  - boundary values (29 vs 31 minutes; 90/91/180/181 days; exactly 100 vs 101 rows)
  - malformed or hostile input (SQL injection attempts, noisy country codes)
  - duplicates
  - partial failure (a crash mid-day, a timeout, Bedrock throttling)
  - limits being exhausted

  Each failure mode the specs name gets a test asserting the documented behaviour (error type, message, no partial writes).
- **Regression tests.** When fixing a bug, first write a test that reproduces it and watch it fail, then fix the bug. Name the test after the behaviour, not the ticket (`test_late_event_lands_in_existing_session`), and keep it forever.
- **Shared builders.** Generator test builders live in `data-platform/generator/tests/helpers.py` as `make_<record>(id, **overrides)`; a test asserts only values it passes at the call site. Snapshots are built only through `make_snapshot` / `shop_snapshot`.
- **Parametrize** when tests share arrange and act and differ in data or the assertion.
- **Slow arrange becomes a fixture.** Arrange over about 1 s that tests only read is a module or session fixture, read-only; a fixture that holds a database compares a fingerprint in its teardown (`month`, `after_sales_season` in `data-platform/generator/tests/conftest.py`).
- **Statistics come from the simulator, never a copy of its day loop.** A rate is measured on the shared month run where its sample suffices (R1, R2, R12), otherwise on a larger run of the simulator that applies the rule (`run_days` in `helpers.py`).
- **Each realism rule gets a pure test and a rate test on generator output.** Where the rule is a per-trial probability the test uses `assert_rate` in `helpers.py` (a `z = 4` binomial bound from the trial count); a rule that is a per-day rounded share (R12) gets a loose bound with a comment.
- **Tests don't write data to `sherpa` or `sherpa_eval`.** Tests that need a database use `temp_database`, and a dbt-running test identifies its own invocation rather than reading "the latest build". The exception is `data-platform/tests/dbt/test_run_results_audit.py`: its dbt runs add rows to `ops.dbt_run_results`, refresh `test_failures` and re-run `grant_read_access` on prod.
- **Mark by need:** `integration` when Postgres is enough, `warehouse` only for built relations or artifacts. Tests of `data-platform/postgres/*.sql` live in `data-platform/tests/postgres/`.
- **Don't test a constant against what defines it** (a rate against the constant it is read from); test it against the spec's value.
- UI test rules:
  - No test directory matches the effective `norecursedirs`: pytest's default list skips `build` and `dist`, so `pyproject.toml` lists the defaults without `build`. `ui/backend/tests/test_layout.py` checks that every `test_*.py` under `ui/backend/tests` is collected.
  - Every Vitest API payload comes from a typed builder over the generated schema (`ui/frontend/tests/fixtures/api.ts`); no literal is passed to a mocked `api`.
  - Sort and filter semantics live in the one shared case file (`ui/parity/sort_filter_cases.json`) that pytest and Vitest both run; a new operator or column type adds cases first.
  - A value sent to Postgres (`set local`, session options) is asserted on the fake connection in the default suite.
  - Every limit or default the spec names has a behaviour test that fails when the value changes.
  - An unreachable database is simulated by making `psycopg.connect` raise, never by connecting to a closed port.
  - No sleep proves that something did not happen: use fake timers or a positive signal.
  - Vitest's `restoreMocks` restores `vi.spyOn` spies after each test; a module-level `vi.fn()` is reset in its file's `beforeEach`. Timeouts are set explicitly, and ELK is mocked outside layout tests.
  - Assert what the user sees or what goes over the wire, not a render count or a library's CSS variable.
  - E2E specs read expected values from the warehouse at run time.
  - A fixture must be able to tell the rule it tests apart from the default.
- **MCP server tests** (`agent/mcp-server/tests/`):
  - Every limit the spec names gets a boundary test on both sides, asserting the spec's literal value, never the module constant.
  - A security allow-list is pinned by an exact-set test, and each rejected family the spec names has at least three members in the rejection table.
  - A defence-in-depth layer is tested with the other layers turned off (the role's privileges with the guard skipped and a read-write session).
  - A refusal test asserts which layer refused (the guard rule, the read-only session or the role's privileges).
  - Tool logic that needs no rows is unit-tested in the default suite on the fixture catalog and an autospec'd `AgentDatabase`.
  - Data-dependent assertions use data that tells the cases apart (values that differ per case, never all-equal rows).
  - Never compare a tool's output with the constant it returns.
  - Timing tests keep a 2× margin or patch the clock.
  - When dbt is bumped, refresh the fixture manifest from a real build.
- Agent tests use PydanticAI `TestModel`/`FunctionModel` with no network; use `AsyncMock` for async collaborators.
- UI tests come in three layers: backend pytest (`ui/backend/tests/`, `warehouse` for the ones that need the real marts), frontend Vitest + Testing Library with mocked API responses (`cd ui/frontend && npm test`), and Playwright against the compose stack (`make e2e`).
- Markers:
  - `integration`: needs Postgres. A test that needs only Postgres and the `sherpa_agent` role (no marts) is `integration`, not `warehouse`, so it runs in the default suite; slow subprocess tests of the MCP server stay `warehouse`
  - `warehouse`: needs the built warehouses (`make build-prod`; the `dev_marts` test also needs `make build`, the eval database tests `make eval-db`). The MCP server tests over the compose container also need `SHERPA_TEST_MCP_CONTAINER=1` and the `mcp` service up; without it they are skipped. The UI's `e2e` Playwright specs need the compose stack up and the prod warehouse built; they run with `make e2e`, not pytest
  - `slow`: full-year runs
  - `live`: real Bedrock calls; costs money, never in CI

  `slow` and `warehouse` tests are excluded by default (`-m "not slow and not warehouse"`). An explicit `-m` replaces that default, so run the integration tests alone with `uv run --env-file .env pytest -m "integration and not slow"` and the warehouse tests with `uv run --env-file .env pytest -m warehouse`.

  Always save `live` output to a file so it can be reviewed without re-running: `uv run pytest -m live 2>&1 | tee /tmp/live_out.txt`.

### Tooling
- Every Python command runs through uv: `uv run pytest`, `uv run python`, `uv add <pkg> --group <group>`.
- ruff rule set: `E, F, I, UP, B, SIM, ASYNC, T20, LOG, G, C4, TC, PT, RUF`. mypy runs on `data-platform/generator/src`, `agent/mcp-server/src` and `ui/backend/src`. The frontend is linted with ESLint (`npm run lint`) and type-checked with `tsc --noEmit` (`npm run typecheck`), both run by `make lint`; they are not pre-commit hooks. pre-commit runs ruff check/format, mypy, sqlfluff, `check-yaml`, `check-toml`, `end-of-file-fixer`, `trailing-whitespace` and `check-added-large-files`.

## Environment notes

- The development machine is Windows with Git Bash. Run `make` from Git Bash: in PowerShell, GNU make runs recipes with `cmd.exe`, and they need a POSIX shell. `make` must be installed separately (Scoop, Chocolatey or WSL).
- GitHub: personal account `josepmafe` on **github.com**, not the corporate GitHub Enterprise host. Commits use the repo-local identity `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`. Don't use the corporate email.
- A new git worktree has none of the gitignored files, so before running anything in it: copy `.env` from the main checkout, run `uv sync`, and for UI work `cd ui/frontend && npm ci`. Work that reads dbt artifacts (`target-prod/`, per checkout too) needs `make build-prod` once. Every worktree needs its own `.venv`: the project is an editable install, so a venv imports the source of the checkout that built it, and the main checkout's venv would run the main checkout's code. On a OneDrive path set `UV_LINK_MODE=copy` (see the README).
- Docker: the compose project is pinned to `sql-sherpa` and the data volume to `sherpa_pgdata`, so every checkout and worktree shares one Postgres container and one warehouse; `make reset` anywhere resets it for all.
- The repo is public. Never commit credentials, corporate data or references to internal systems.
