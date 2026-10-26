.PHONY: up down generate backfill reset build build-prod docs eval-db mcp-dev ui-client ui-dev e2e lint test
# Private targets, called by the ones above and not commands of their own; their names start with _.
.PHONY: _roles _docs-check _lockfile-check _ui-api-dev _ui-web-dev

# dbt runs from data-platform/dbt/ with the profile in the same folder; --env-file supplies the SHERPA_* variables profiles.yml reads.
DBT = cd data-platform/dbt && DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt
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
DBT_SCHEMAS = dev_seeds, dev_staging, dev_intermediate, dev_marts, dev_dbt_test__audit, seeds, staging, intermediate, marts, dbt_test__audit, ops
# The dbt packages, installed again whenever packages.yml or package-lock.yml changes; every target that runs dbt
# depends on them.
DBT_PACKAGES = data-platform/dbt/dbt_packages/.installed

# After the services are healthy, the roles are brought up to date (_roles), so an existing volume never lags behind.
up:
	docker compose up -d --build --wait
	$(MAKE) --no-print-directory _roles

down:
	docker compose down

generate:
	$(if $(DAYS),,$(error DAYS is required, e.g. make generate DAYS=3))
	uv run sherpa-generate generate --days $(DAYS)

backfill: DAYS ?= 365
backfill:
	uv run sherpa-generate generate --days $(DAYS)

# The dbt schemas go first: their views read from the raw schemas.
reset:
	docker compose exec -T postgres sh -c \
		'psql -U "$$SHERPA_ADMIN_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP SCHEMA IF EXISTS $(DBT_SCHEMAS) CASCADE"'
	uv run sherpa-generate reset

# dbt build loads the seeds too. It does not check source freshness, so that runs first (warnings pass, errors stop
# the build).
build: $(DBT_PACKAGES)
	$(DBT) source freshness
	$(DBT) build

build-prod: $(DBT_PACKAGES)
	$(DBT) source freshness $(PROD)
	$(DBT) build $(PROD)
	$(DBT) docs generate $(PROD)

docs: $(DBT_PACKAGES)
	$(DBT) docs generate
	$(DBT) docs serve

# Rebuilds the frozen eval database from scratch (fixed seed and dates); never touches the main database.
# No source freshness: the frozen data is old on purpose. Inside the container, POSTGRES_DB is the main database,
# whatever make saw as SHERPA_DB_NAME. The new database's public schema and temporary tables are locked down like the
# main one's, so the agent only reaches the marts; the large-object revokes come with template1 (init script).
eval-db: $(DBT_PACKAGES)
	$(if $(and $(filter 1,$(words $(SHERPA_EVAL_DB_NAME))),$(filter sherpa_eval%,$(SHERPA_EVAL_DB_NAME)),$(filter-out $(SHERPA_DB_NAME),$(SHERPA_EVAL_DB_NAME))),,$(error SHERPA_EVAL_DB_NAME must start with sherpa_eval and differ from the main database '$(SHERPA_DB_NAME)', got '$(SHERPA_EVAL_DB_NAME)'))
	$(if $(call strip-chars,$(SHERPA_EVAL_DB_NAME),$(DB_NAME_CHARS)),$(error SHERPA_EVAL_DB_NAME may only contain [a-z0-9_], got '$(SHERPA_EVAL_DB_NAME)'))
	docker compose exec -T postgres sh -c \
		'test "$$POSTGRES_DB" != $(SHERPA_EVAL_DB_NAME) || { echo "refusing to drop the main database $$POSTGRES_DB" >&2; exit 1; }; \
		psql -U "$$SHERPA_ADMIN_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $(SHERPA_EVAL_DB_NAME) WITH (FORCE)" -c "CREATE DATABASE $(SHERPA_EVAL_DB_NAME)" && \
		psql -U "$$SHERPA_ADMIN_USER" -d $(SHERPA_EVAL_DB_NAME) -v ON_ERROR_STOP=1 -c "REVOKE ALL ON SCHEMA public FROM PUBLIC" -c "REVOKE TEMPORARY ON DATABASE $(SHERPA_EVAL_DB_NAME) FROM PUBLIC"'
	SHERPA_DB_NAME=$(SHERPA_EVAL_DB_NAME) uv run sherpa-generate generate --seed 42 --start-date 2025-01-01 --days 365
	$(DBT) build $(EVAL)
	$(DBT) docs generate $(EVAL)

# The MCP server on the host over Streamable HTTP (http://127.0.0.1:8765/mcp), e.g. for MCP Inspector.
mcp-dev:
	MCP_TRANSPORT=http uv run --env-file .env sherpa-mcp

# The OpenAPI schema goes through a temp file (inside the gitignored node_modules, so Python and Node see the same
# path on every OS); the committed client types are generated from it.
UI_SCHEMA = ui/frontend/src/api/schema.d.ts
UI_TMP = ui/frontend/node_modules/.cache/sherpa
UI_OPENAPI = $(UI_TMP)/openapi.json

ui-client:
	uv run python -m sherpa_ui.openapi_export $(UI_OPENAPI)
	cd ui/frontend && npx openapi-typescript ../../$(UI_OPENAPI) -o src/api/schema.d.ts

# The UI's port and login as compose reads them: from the environment or the make command line, else from .env, else
# the default. make reads only these from .env, for the Node tools; uv loads the rest for the Python ones.
dotenv-value = $(shell sed -n 's/^$(1)=//p' .env 2>/dev/null | tail -n 1 | tr -d '\r"')
UI_PORT = $(or $(SHERPA_UI_PORT),$(call dotenv-value,SHERPA_UI_PORT),8090)
UI_USER = $(or $(SHERPA_UI_USER),$(call dotenv-value,SHERPA_UI_USER),sherpa_ui)

# The API (reloading, on SHERPA_UI_PORT) and the Vite dev server, which proxies /api to it.
ui-dev:
	$(MAKE) -j2 _ui-api-dev _ui-web-dev

# Playwright against the compose stack (`make up`, `make build-prod`) on SHERPA_UI_PORT. The freshness spec skips
# itself here: it adds a simulated day and rebuilds prod, so it runs only when SHERPA_E2E_FRESHNESS=1 is set.
e2e:
	cd ui/frontend && SHERPA_UI_PORT=$(UI_PORT) SHERPA_UI_USER=$(UI_USER) npx playwright test

# Every static check. The committed API client must match the one the backend's OpenAPI schema generates.
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run --env-file .env sqlfluff lint data-platform/dbt
	$(MAKE) --no-print-directory _docs-check
	cd ui/frontend && npm run lint && npm run typecheck
	$(MAKE) --no-print-directory _lockfile-check
	uv run python -m sherpa_ui.openapi_export $(UI_OPENAPI)
	cd ui/frontend && npx openapi-typescript ../../$(UI_OPENAPI) -o ../../$(UI_TMP)/schema.d.ts
	diff $(UI_SCHEMA) $(UI_TMP)/schema.d.ts

test:
	uv run --env-file .env pytest

# --- Private targets ---

# The dbt packages, installed again whenever packages.yml or package-lock.yml is newer than the last install.
$(DBT_PACKAGES): data-platform/dbt/packages.yml data-platform/dbt/package-lock.yml
	$(DBT) deps
	touch $@

# The roles, with the SQL the init script runs on a new volume: it creates missing roles and resets passwords and
# settings from the running container's environment (.env), and never fails on a re-run.
_roles:
	docker compose exec -T postgres sh -c \
		'psql -U "$${POSTGRES_USER:-postgres}" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -q \
		-v admin_user="$$SHERPA_ADMIN_USER" -v admin_password="$$SHERPA_ADMIN_PASSWORD" \
		-v agent_user="$$SHERPA_AGENT_USER" -v agent_password="$$SHERPA_AGENT_PASSWORD" \
		-v ui_user="$$SHERPA_UI_USER" -v ui_password="$$SHERPA_UI_PASSWORD" \
		-v db_name="$$POSTGRES_DB" -f /sherpa/roles.sql'
	docker compose exec -T postgres sh -c \
		'for d in "$$POSTGRES_DB" template1; do \
		psql -U "$${POSTGRES_USER:-postgres}" -d "$$d" -v ON_ERROR_STOP=1 -q -f /sherpa/large_objects.sql; done'

# Every model and column under models/ is described: docs generate writes the YAML (manifest.json) and the real
# columns (catalog.json) into the dev target, and the checker compares them.
_docs-check: $(DBT_PACKAGES)
	$(DBT) --quiet docs generate
	uv run python data-platform/scripts/check_docs_coverage.py --target-path data-platform/dbt/target --path models

# The npm lockfile resolves packages from the public registry only (entries without a `resolved` field are fine).
_lockfile-check:
	@if grep '"resolved"' ui/frontend/package-lock.json | grep -qv '"resolved": "https://registry.npmjs.org/'; then \
		echo "ui/frontend/package-lock.json has a package resolved outside https://registry.npmjs.org/" >&2; exit 1; fi

# The two halves of ui-dev.
_ui-api-dev:
	uv run --env-file .env uvicorn sherpa_ui.app:dev_app --reload --port $(UI_PORT)

_ui-web-dev:
	cd ui/frontend && SHERPA_UI_PORT=$(UI_PORT) npm run dev
