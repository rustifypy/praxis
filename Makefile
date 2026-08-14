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

# The other half of `test`: what a real sandbox container does, which the suite deliberately
# does not mock. Needs docker; builds the image on its first run. Uses a scratch DATA_DIR, so
# it never touches ~/.analyst.
#   make sandbox-check ARGS="--stale"        also prove recovery from a replaced mount
#   make sandbox-check ARGS='--ask "..."'    one real turn, needs model credentials
ARGS ?=
sandbox-check:
	uv run python scripts/sandbox_check.py $(ARGS)

fmt:
	uv run ruff format . && uv run ruff check --fix .

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

.PHONY: install up down logs test lint fmt sandbox-check api web-build web serve
