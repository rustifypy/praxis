install:
	uv sync --all-groups

test:
	uv run pytest -q

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

.PHONY: install test lint fmt api web-build web serve
