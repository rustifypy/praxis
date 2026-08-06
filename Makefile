install:
	uv sync --all-groups

COMPOSE = docker compose -f docker-compose.sources.yml

up:
	$(COMPOSE) up -d
	@uv run analyst source ls

down:
	$(COMPOSE) down

sources:
	uv run analyst source ls --check

# make logs SOURCE=commerce
SOURCE ?= commerce

logs:
	$(COMPOSE) logs -f cube-$(SOURCE)

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

catalog:
	uv run analyst catalog

api:
	uv run uvicorn analyst_agent.api:app --reload --port 8000

.PHONY: install up down sources logs test lint fmt catalog api
