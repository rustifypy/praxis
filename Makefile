install:
	uv sync --all-groups

test:
	uv run pytest -q

sandbox-test:
	cd services/sandbox && uv run pytest -q

store-test:
	cd services/semantic/store && uv run pytest -q

ARGS ?=
store-check:
	bash services/semantic/tests/store_check.sh $(ARGS)

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

src/web/node_modules: src/web/package-lock.json
	cd src/web && npm ci
	touch src/web/node_modules

web-build: src/web/node_modules
	cd src/web && npm run build

web: web-build serve

serve:
	uv run analyst

.PHONY: install test lint fmt api web-build web serve store-test sandbox-test store-check
