.PHONY: up down generate backfill status reset deps seed build build-prod docs docs-prod grant-agent docs-check lint test

# dbt runs from dbt/ with the profile in the same folder; --env-file supplies the SHERPA_* variables profiles.yml reads.
DBT = cd dbt && DBT_PROFILES_DIR=. uv run --env-file ../.env dbt
# Prod artifacts (manifest.json, catalog.json, ...) go to their own folder so dev runs never overwrite them.
PROD = --target prod --target-path target-prod
DBT_SCHEMAS = dev_seeds, dev_staging, dev_intermediate, dev_marts, dev_dbt_test__audit, seeds, staging, intermediate, marts, dbt_test__audit

up:
	docker compose up -d --wait

down:
	docker compose down

generate:
	$(if $(DAYS),,$(error DAYS is required, e.g. make generate DAYS=3))
	uv run sherpa-generate generate --days $(DAYS)

backfill: DAYS ?= 365
backfill:
	uv run sherpa-generate generate --days $(DAYS)

status:
	uv run sherpa-generate status

# The dbt schemas go first: their views read from the raw schemas.
reset:
	docker compose exec -T postgres sh -c \
		'psql -U "$$SHERPA_ADMIN_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS $(DBT_SCHEMAS) CASCADE"'
	uv run sherpa-generate reset

deps:
	$(DBT) deps

seed:
	$(DBT) seed

# dbt build does not check source freshness, so it runs first (warnings pass, errors stop the build).
build:
	$(DBT) source freshness
	$(DBT) build

build-prod:
	$(DBT) source freshness $(PROD)
	$(DBT) build $(PROD)

# Re-applies the agent's prod grants without a build (same macro as the on-run-end hook).
grant-agent:
	$(DBT) run-operation grant_agent_access $(PROD)

docs:
	$(DBT) docs generate
	$(DBT) docs serve

docs-prod:
	$(DBT) docs generate $(PROD)
	$(DBT) docs serve $(PROD)

# docs generate writes manifest.json (the YAML) and catalog.json (the real columns); the checker compares them.
docs-check:
	$(DBT) --quiet docs generate
	uv run python scripts/check_docs_coverage.py --target-path dbt/target --path models

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run --env-file .env sqlfluff lint dbt

test:
	uv run --env-file .env pytest
