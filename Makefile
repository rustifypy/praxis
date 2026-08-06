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

# Frontend source is src/web; the output is committed into the package so that
# `analyst web` needs no node toolchain on the machine that runs it.
web:
	cd src/web && npm ci && npm run build

serve:
	uv run analyst web

.PHONY: install up down sources logs test lint fmt catalog api web serve
