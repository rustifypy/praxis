install:
	uv sync --all-groups

# Only the shared Cube Store lives in compose. Source containers are started from
# their rows in sources.db, by the portal — `make serve`, then Data in the rail.
COMPOSE = docker compose -f docker-compose.yml

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

# Source containers are `cube-<name>`; the portal shows the same output under Output.
# make logs SOURCE=commerce
SOURCE ?= commerce

logs:
	docker logs --tail 200 -f cube-$(SOURCE)

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

api:
	uv run uvicorn analyst_agent.api:app --reload --port 8000

# A real target, not phony: `npm ci` wipes node_modules and reinstalls, which is right
# when the lockfile moves and wasteful on every serve. Make reruns it only when it does.
src/web/node_modules: src/web/package-lock.json
	cd src/web && npm ci
	touch src/web/node_modules

# Frontend source is src/web; the output is committed into the package so that
# `analyst` needs no node toolchain on the machine that runs it. Kept separate from
# `web` because regenerating that committed artifact must not start a blocking server.
web-build: src/web/node_modules
	cd src/web && npm run build

web: web-build serve

serve:
	uv run analyst

.PHONY: install up down logs test lint fmt api web-build web serve
