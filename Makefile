.PHONY: up down mutate

up:
	docker compose up -d --wait

down:
	docker compose down

# mutmut 3 mutates every file under source_paths; the glob (mutant names are dotted module paths) scopes the run.
MUTANT_GLOB = *$(subst /,.,$(patsubst %.py,%,$(patsubst generator/src/%,%,$(MODULE)))).*

mutate:
	uv run mutmut run "$(MUTANT_GLOB)" && uv run mutmut results
