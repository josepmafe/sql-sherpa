.PHONY: up down generate backfill status reset deps seed build build-prod docs docs-prod grant-agent docs-check eval-db lint test

# dbt runs from dbt/ with the profile in the same folder; --env-file supplies the SHERPA_* variables profiles.yml reads.
DBT = cd dbt && DBT_PROFILES_DIR=. uv run --env-file ../.env dbt
# Prod artifacts (manifest.json, catalog.json, ...) go to their own folder so dev runs never overwrite them.
PROD = --target prod --target-path target-prod
# The database names are read by make from the environment or the command line (defaults sherpa_eval and sherpa);
# make does not read .env for them. eval-db refuses eval names that do not start with sherpa_eval, contain anything
# but [a-z0-9_] (so they need no quoting in sh or SQL) or equal the main database name.
# Eval artifacts and database: a frozen warehouse (separate database) built by `make eval-db`.
EVAL = --target eval --target-path target-eval
SHERPA_EVAL_DB_NAME ?= sherpa_eval
export SHERPA_EVAL_DB_NAME
SHERPA_DB_NAME ?= sherpa
DB_NAME_CHARS = a b c d e f g h i j k l m n o p q r s t u v w x y z 0 1 2 3 4 5 6 7 8 9 _
# $(call strip-chars,text,chars): text with every character in the word list chars removed.
strip-chars = $(if $(2),$(call strip-chars,$(subst $(firstword $(2)),,$(1)),$(wordlist 2,$(words $(2)),$(2))),$(1))
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

# Rebuilds the frozen eval database from scratch (fixed seed and dates); never touches the main database.
# No source freshness: the frozen data is old on purpose. Inside the container, POSTGRES_DB is the main database,
# whatever make saw as SHERPA_DB_NAME. The new database's public schema and temporary tables are locked down like the
# main one's, so the agent only reaches the marts; the large-object revokes come with template1 (init script).
eval-db:
	$(if $(and $(filter 1,$(words $(SHERPA_EVAL_DB_NAME))),$(filter sherpa_eval%,$(SHERPA_EVAL_DB_NAME)),$(filter-out $(SHERPA_DB_NAME),$(SHERPA_EVAL_DB_NAME))),,$(error SHERPA_EVAL_DB_NAME must start with sherpa_eval and differ from the main database '$(SHERPA_DB_NAME)', got '$(SHERPA_EVAL_DB_NAME)'))
	$(if $(call strip-chars,$(SHERPA_EVAL_DB_NAME),$(DB_NAME_CHARS)),$(error SHERPA_EVAL_DB_NAME may only contain [a-z0-9_], got '$(SHERPA_EVAL_DB_NAME)'))
	docker compose exec -T postgres sh -c \
		'test "$$POSTGRES_DB" != $(SHERPA_EVAL_DB_NAME) || { echo "refusing to drop the main database $$POSTGRES_DB" >&2; exit 1; }; \
		psql -U "$$SHERPA_ADMIN_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $(SHERPA_EVAL_DB_NAME) WITH (FORCE)" -c "CREATE DATABASE $(SHERPA_EVAL_DB_NAME)" && \
		psql -U "$$SHERPA_ADMIN_USER" -d $(SHERPA_EVAL_DB_NAME) -v ON_ERROR_STOP=1 -c "REVOKE ALL ON SCHEMA public FROM PUBLIC" -c "REVOKE TEMPORARY ON DATABASE $(SHERPA_EVAL_DB_NAME) FROM PUBLIC"'
	SHERPA_DB_NAME=$(SHERPA_EVAL_DB_NAME) uv run sherpa-generate generate --seed 42 --start-date 2025-01-01 --days 365
	$(DBT) build $(EVAL)
	$(DBT) docs generate $(EVAL)

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run --env-file .env sqlfluff lint dbt

test:
	uv run --env-file .env pytest
