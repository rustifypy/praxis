# Contributing

## Setup

```bash
cp .env.example .env      # Azure OpenAI key + endpoint; check QUERY_MODEL
make install              # uv sync --all-groups
```

You need Docker and a database of your own — nothing is bundled. Point the tool at it:

```bash
uv run analyst source add
```

That writes `sources.yml`, `.env.sources/<name>.env` (chmod 600, git-ignored), an empty
`cube/sources/<name>/`, and a regenerated `docker-compose.sources.yml`, then starts the
container and verifies the connection through Cube's `/readyz`.

A database running on your machine is `host.docker.internal` from inside the container,
not `localhost`. On Intel or AMD, set `CUBESTORE_TAG=latest` in `.env` — Cube Store has
no multi-arch `latest` tag and the default is `arm64v8`.

## The loop

```bash
make test                 # 84 tests, no containers, no model calls — sub-second
make lint                 # ruff check + format --check
make fmt                  # fix what is fixable
```

Run `make lint` before opening a PR; CI runs the same commands.

Anything that talks to a container or the model is **not** covered by the test suite.
Verify that by running it and checking the result:

```bash
uv run analyst source model <name> --enrich --yes
uv run analyst ask "..." --sql
```

and compare the answer against the database directly. Several real bugs in this
repository were caught that way and would not have been caught by a mock.

## Code style

Ruff, line length 100. Beyond that, two habits this codebase holds to:

**Inline single-caller helpers.** A private function with one call site is a hop the
reader has to follow for no benefit. The pipeline in `sources/model/` is procedural on
purpose — it reads top to bottom. Helpers earn their place at two or more callers, and
especially when they exist so two code paths cannot drift apart.

**Say why, not what.** Most comments here record a Cube behaviour that forced a decision:
why joins are dropped without a primary key, why expressions are `{CUBE}`-qualified but
bare columns are not, why validation restarts a container. Those are the comments worth
writing.

## Adding a warehouse

Two independent pieces, and you can do the first without the second.

**Connecting** — add a `Driver` to `sources/drivers.py` with its `CUBEJS_DB_*` fields.
Field names are Cube's own, so anything Cube documents works verbatim. The source will
connect and serve a hand-written model. Drivers needing a different image or platform set
`image` / `platform`; a field holding a host path (a key file) sets `mount_to`.

**Extracting tables** — add a `Dialect` to `sources/model/read.py` with SQL returning
`KEY_COLUMNS` (and optionally `STAT_COLUMNS`). Those SELECTs run as probe cubes through
Cube's own driver, so no new database client is needed. `DIALECTS` is the single list of
drivers that can extract tables — the CLI's `(no table extraction yet)` label reads from
it, so adding an entry is all that is required.

Statistics are optional. Without them the model still generates; the LLM just has less to
go on when deciding which numbers are worth aggregating.

## Changing model generation

`sources/model/` has one module per stage and `run.generate` holds the sequence. Before
changing it, know the two invariants:

1. **The LLM returns decisions, never YAML.** It picks from a menu derived from the
   database, and every choice is checked against that menu before anything is written. A
   hallucinated join is a silently wrong number.
2. **Cube is the validator.** Candidate models go to the real container and Cube reports
   what is wrong. Do not add a YAML checker — it would be a second, weaker opinion about
   what Cube accepts.

New guards on the model's output belong in `MEASURE_RULES` in `decide.py`: a rule id, a
reason, a predicate. The id shows up in the rejection line, so failures stay countable.

## Pull requests

Say what changed and how you verified it. For anything container- or model-dependent,
include the actual output — the numbers you got and what you compared them against. If
you removed a test, say what still covers the behaviour.
