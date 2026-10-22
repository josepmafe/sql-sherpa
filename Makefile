.PHONY: up down generate backfill status reset mutate deps seed

# dbt runs from dbt/ with the profile in the same folder; --env-file supplies the SHERPA_* variables profiles.yml reads.
DBT = cd dbt && DBT_PROFILES_DIR=. uv run --env-file ../.env dbt

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

reset:
	uv run sherpa-generate reset

# mutmut 3 mutates every file under source_paths; the glob (mutant names are dotted module paths) scopes the run.
MUTANT_GLOB = *$(subst /,.,$(patsubst %.py,%,$(patsubst generator/src/%,%,$(MODULE)))).*

mutate:
	uv run mutmut run "$(MUTANT_GLOB)" && uv run mutmut results

deps:
	$(DBT) deps

seed:
	$(DBT) seed
