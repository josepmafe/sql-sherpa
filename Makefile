.PHONY: up down

up:
	docker compose up -d --wait

down:
	docker compose down
