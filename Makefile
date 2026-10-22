.PHONY: up down mutate

up:
	docker compose up -d --wait

down:
	docker compose down

mutate:
	uv run mutmut run --paths-to-mutate $(MODULE) && uv run mutmut results
