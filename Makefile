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

# Run through uvx rather than added to the project: the docs toolchain has no business in uv.lock.
MKDOCS = uvx --with 'mkdocs-material>=9.5,<10' --with 'mkdocs<2' mkdocs

# 8001, because the model store already holds mkdocs' default 8000.
docs:
	$(MKDOCS) serve -a 127.0.0.1:8001

docs-build:
	$(MKDOCS) build

.PHONY: install test lint fmt api web-build web serve docs docs-build
