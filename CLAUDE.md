# CLAUDE.md

SQL Sherpa is a **toy project held to production standards**: a synthetic e-commerce warehouse (generator + dbt on Postgres) and a text-to-SQL agent on top of it. The owner is learning dbt (new to them) and building their first text-to-SQL agent (they already know agentic systems). Explain dbt concepts when they come up; don't over-explain generic agent plumbing.

## Source of truth

- **Data platform spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`. The data model, metric definitions, realism rules and dbt feature roadmap live there.
- **Spec compliance:** the specs win over the plans. If a plan conflicts with a spec, follow the spec and say so. Deviate from a spec only when the owner explicitly asks, and then update the spec once the task is done.
- **Agent spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`. Relies only on the interface in the data platform spec §11.
- **Milestones and plans:** the data platform is built in milestones `data-platform-1` … `data-platform-5`, and the agent in `agent-design-1` … `agent-design-10` (each spec's §8). Each milestone has one plan in `docs/superpowers/plans/`, named `<date>-data-platform-<n>-<name>.md` or `<date>-agent-design-<n>-<name>.md`. A plan is written **just before** its milestone is built, never all upfront. data-platform-1 is done (`2026-10-22-data-platform-1-foundation.md`); data-platform-2 is next and still has to be planned.
- Sequencing: data-platform-1 → data-platform-2 (defines the agent-facing interface) → agent-design-1 to agent-design-3 alongside data-platform-3 → data-platform-4 → agent-design-4 (gold set batch 2) → agent-design-5 to agent-design-9; data-platform-5 → agent-design-10.
- **Every plan ends with a documentation task**, and writing a plan includes writing it. The task does two things:
  - **Checks `CLAUDE.md` and `README.md`** against what the milestone changed (commands, layout, status, conventions) and updates them.
  - **Writes or updates how-to guides** in `docs/guides/`:
    - `basic/`: how to *use* what the milestone built, with easy and complete, copy-pasteable examples.
    - `advanced/`: how to *extend or modify* it, i.e. which files to touch, in which order, and which tests prove the change.

    Every milestone that adds something a person runs or extends gets at least one guide of each kind, or updates existing guides. Guides follow the current-state rule like every other doc.
- **Docs describe the current state only.** `CLAUDE.md`, the README, the specs and the plans say what *is*, not what used to be. When something changes, rewrite the affected text in place. Don't add "previously…", "no longer…", "was replaced by…" or notes about discarded approaches; git history is the archive. Anything removed was removed on purpose, so don't carry it over.

## Principles

- **Every dbt feature has a real purpose.** Features are introduced in the milestone where they first do a job (the data platform spec §8). Don't pull later-milestone features forward without asking.
- **Keep the generator simple.** It is a day-by-day simulation with a fixed list of realism rules R1–R12 (the data platform spec §4.4). Don't add rules or knobs beyond the spec.
- **The agent is built incrementally and measured.** Start from a single-agent baseline and add one technique at a time. Each technique has to improve the eval scores. Keep the eval code small and boring.
- **Observability and evals are not afterthoughts.** Tracing comes with the first agent code, and the eval harness comes before any pipeline complexity.

## Commands

- `make up` / `make down`: start / stop the Postgres container.
- `make generate DAYS=<n>`: simulate `n` days into the raw schemas (`DAYS` is required).
- `make backfill`: simulate `DAYS` days (default 365) into the raw schemas.
- `make status`: print the simulated date range and the row count of every raw table.
- `make reset`: drop the dbt schemas (including the `--store-failures` audit schema) and the raw schemas, then recreate the raw schemas empty.
- `make deps`: install the dbt packages.
- `make seed`: load the dbt seeds.
- `make build`: check source freshness, then `dbt build` on the dev target.
- `make build-prod`: the same on the prod target, with artifacts in `target-prod`.
- `make docs` / `make docs-prod`: generate and serve the dbt docs (dev / prod).
- `make docs-check`: fail if a staging model or column has no description.
- `make lint`: ruff check, ruff format check, mypy and sqlfluff.
- `make test`: pytest with `.env` loaded (`slow` tests excluded); the integration tests need Postgres (`make up`).
- `make mutate MODULE=<path>`: run mutmut on one generator module.

## Conventions

- Python 3.12, uv, single root `pyproject.toml` (dependency groups `generator`, `dbt`, `dev`). Lint/format with ruff; SQL with sqlfluff (dbt templater, Postgres dialect).
- dbt naming: `stg_<source>__<entity>`, `int_<entity>__<verb>`, `fct_` / `dim_` / `obt_`. YAML per directory: `_<dir>__models.yml`, `_<source>__sources.yml`. Every model and column gets a description.
- Schemas: `dev_`-prefixed on the `dev` target, verbatim on `prod`/`ci`, snapshots always in `snapshots`. Prod dbt commands use `--target-path target-prod`.
- Generator determinism: never use Python's built-in `hash()` (use `stable_int` / `DeterministicRandom`). The generator assigns its own primary keys. Nothing may be timestamped after yesterday (UTC).
- Time-relative columns use `as_of_date`, never `now()`.
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
  - MCP server: `sql_guard`, `masking` and `manifest` parsing are pure; tools and `db` do the I/O
  - agent: `guards` and `outcomes` are pure; variants orchestrate
- One package per component (`sherpa_generator`, `sherpa_mcp`, `sherpa_agent`), each with its own `settings.py` (pydantic-settings, `frozen=True`, env prefix `SHERPA_`). Read configuration only through it; never call `os.environ` elsewhere.
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
- Stdlib `logging` only: `logger = logging.getLogger(__name__)` per module, with `%`-style arguments (`logger.info("wrote %d rows", n)`, not f-strings). No `print()` outside CLI output (Typer/Rich) and `scripts/`.
- **The MCP server never writes to stdout** (it carries the JSON-RPC stream): logs go to stderr, and Logfire runs with `console=False`.

### Formatting
- ruff format; line length 120 (prompt strings in agent modules may exceed it).
- Leave a blank line after a `with`, `if` or `try/except` block before the next statement, but not before the block.
- Docstrings only where the *why* isn't obvious from the name and signature; keep them to a few lines. Comments explain intent and constraints, not what the code does.

### Tests
- `tests/` mirror the package layout. For a class: one test class per target class, with one or more test methods per method under test. For a function: top-level test functions.
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
- **Mutation tests** where correctness hinges on small details: discount allocation and rounding, `sql_guard`, result comparison (`compare.py`), the answer guards, and the realism rules' rates. Run `make mutate MODULE=<path>` on those modules. Surviving mutants either get a test that kills them or a short justification in the test file. Mutation runs are slow, so they are run on demand and not in CI.
- Agent tests use PydanticAI `TestModel`/`FunctionModel` with no network; use `AsyncMock` for async collaborators.
- Markers:
  - `integration`: needs Postgres
  - `slow`: full-year runs; excluded by default (`-m "not slow"`). An explicit `-m` replaces that default, so run the integration tests alone with `uv run pytest -m "integration and not slow"`
  - `live`: real Bedrock calls; costs money, never in CI

  Always save `live` output to a file so it can be reviewed without re-running: `uv run pytest -m live 2>&1 | tee /tmp/live_out.txt`.

### Tooling
- Every Python command runs through uv: `uv run pytest`, `uv run python`, `uv add <pkg> --group <group>`.
- `mutmut` is in the `dev` group; `make mutate MODULE=<path>` runs it on one module.
- ruff rule set: `E, F, I, UP, B, SIM, ASYNC, T20, LOG, G, C4, TC, PT, RUF`. mypy runs on `generator/src`. pre-commit runs ruff check/format, mypy, sqlfluff, `check-yaml`, `check-toml`, `end-of-file-fixer`, `trailing-whitespace` and `check-added-large-files`.

## Environment notes

- The development machine is Windows with Git Bash. `make` must be installed separately (Scoop, Chocolatey or WSL). mutmut does not run on native Windows; use WSL or Linux for `make mutate`.
- GitHub: personal account `josepmafe` on **github.com**, not the corporate GitHub Enterprise host. Commits use the repo-local identity `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`. Don't use the corporate email.
- The repo is public. Never commit credentials, corporate data or references to internal systems.
