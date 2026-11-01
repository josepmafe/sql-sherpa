# CI: the pull-request gate

`.github/workflows/ci.yml` runs on every pull request to `main`. Its job is one gate: if it is green, the branch builds on a fresh warehouse and passes the repository's own checks. The design is in the [data platform spec §8](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) ("data-platform-4"). Read [Building the warehouse](../basic/building-the-warehouse.md) first.

## What it runs

In order, on an Ubuntu runner:

1. `cp .env.example .env` and `docker compose up -d --wait postgres`: the same Postgres as locally, so the init script creates the roles. The `change-me` passwords belong to a throwaway container; the workflow has no secrets.
2. `uv sync --locked`, `make data-platform/dbt/dbt_packages/.installed` (the dbt packages, the Makefile's own install step: the backfill script parses the dbt project) and `npm ci`.
3. **Data:** `backfill.py --days 30 --scale 0.05`, then `sherpa-generate start-date` into `SHERPA_SIMULATION_START_DATE`.
4. **dbt** on the `dev` target: `dbt seed`, `dbt run --empty`, then `dbt build --full-refresh --selector ci --warn-error`.
5. `make lint`, `make test` and the frontend's `npm test`.

The warehouse tests (`-m warehouse`), Playwright (`make e2e`) and `live` tests stay local: CI has no prod warehouse or Bedrock. For the same reason `make slim` is local only.

Why the dbt step is shaped this way:

- **`run --empty` first** creates every relation without rows, so a SQL or contract error fails in seconds, before the data build.
- **`--full-refresh`** is required after it: the empty run leaves the incremental models existing but empty, and a plain `dbt build` would then load only the last 7 days, so `assert_every_source_event_is_staged` fails.
- **`--selector ci`** (`data-platform/dbt/selectors.yml`) is everything except tests tagged `distribution`. Those check the generator's random draws against full-year targets (row counts, means, the Pareto share), which 30 days at scale 0.05 cannot meet, and they say nothing about the code under review. Only those tests carry the tag ([Adding or changing a mart](adding-or-changing-a-mart.md#5-write-a-custom-generic-test)).
- **`--warn-error`** turns every dbt warning into an error: a warn-severity test, an unmapped channel, a dbt deprecation. Fix the cause, or change the test's severity on purpose.

## Reproduce a failing step locally

| Failing step | Run |
|---|---|
| Lint, Test, Frontend tests | `make lint`, `make test`, `cd ui/frontend && npm test` |
| dbt | from `data-platform/dbt/` with the usual prefix: `dbt build --full-refresh --selector ci --warn-error` |
| Data | `uv run --env-file .env python data-platform/scripts/backfill.py --days 30 --scale 0.05` on an empty database (`make reset` first; it resets the shared warehouse) |

The dbt step on your full-size dev data is the same code with more rows. To also see the empty run: `dbt run --empty` first.

## Add a check

- **A test of the code:** add it where it belongs (a dbt test, a pytest, a Vitest). `make lint` and `make test` run it, so CI does too. Nothing in the workflow changes.
- **A new step:** add it to `ci.yml` after the step it depends on, and make it runnable locally with a documented command, so a failure can be reproduced. A new GitHub action is pinned to a full commit SHA with its version tag in a comment, and the file carries no secret, host name or proxy setting (the repository is public).
- **A test that only passes on a full year of data:** tag it `distribution`.

## Errors

| Symptom | Fix |
|---|---|
| dbt step: `assert_every_source_event_is_staged` fails | The build ran without `--full-refresh` after `run --empty`; add it |
| dbt step fails on a warning (`Encountered an error` naming a deprecation or a `WARN` test) | Reproduce with `--warn-error` locally and fix the cause |
| A `distribution` test fails in CI | It should not run there: `dbt ls --selector ci --resource-type test` must not list it |
| Data step: `start-date` fails | The backfill wrote nothing; read the earlier lines of the step |
| Lint passes locally and fails in CI | `uv sync --locked` and `npm ci` use the lockfiles; commit `uv.lock` and `package-lock.json` |
