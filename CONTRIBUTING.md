# Contributing

## Setup

```bash
cp .env.example .env      # Azure OpenAI key + endpoint; check QUERY_MODEL
make install              # uv sync --all-groups
```

You need Docker and a database of your own — nothing is bundled. Point the tool at it
from the portal: `make serve`, then **Sources** in the sidebar, **Add source**.

That writes one row to `~/.analyst/sources.db` (credentials included, chmod 600) and an
empty `cube/sources/<name>/`, then starts the container from that row and verifies the
connection through Cube's `/readyz`. There is no registry file and no credential file:
`sources/docker.py` builds a `docker run` from the row, so there is nothing on disk that
can drift from it.

The portal is the only surface for this — `POST /api/sources` is the sole writer of a new
row, and `web/sources.py` the sole caller of `run.action`. A parallel CLI existed and was
removed: the connection form, its validation and the four steps' confirmations were being
written twice, which is exactly how two implementations of one rule start disagreeing.

`make up` starts the Cube Store every source shares; source containers restart with
Docker on their own.

A database running on your machine is `host.docker.internal` from inside the container,
not `localhost`. On Intel or AMD, set `CUBESTORE_TAG=latest` in `.env` — Cube Store has
no multi-arch `latest` tag and the default is `arm64v8`.

## The loop

```bash
make test                 # 165 tests, no containers, no model calls — sub-second
make lint                 # ruff check + format --check
make fmt                  # fix what is fixable
```

Run `make lint` before opening a PR; CI runs the same commands.

Anything that talks to a container or the model is **not** covered by the test suite.
Verify that by running it and checking the result:

```bash
make web                     # Sources → run the four steps on a real database, then ask
```

and compare the answer against the database directly. The chat's tool calls show the Cube
query and the SQL it compiled to. Several real bugs in this
repository were caught that way and would not have been caught by a mock.

**Never run the `cubes` step against a source whose model you did not build.** It rewrites
every YAML file in `cube/sources/<name>/`, views and descriptions included, and those cost
one model call per table to rebuild. Add a throwaway source pointing at the same database
instead. Nothing in the tool undoes it — commit the model directory first, because `git`
is the only way back.

## Code style

Ruff, line length 100. Beyond that, two habits this codebase holds to:

**Inline single-caller helpers.** A private function with one call site is a hop the
reader has to follow for no benefit. The pipeline in `sources/model/` is procedural on
purpose — it reads top to bottom. Helpers earn their place at two or more callers, and
especially when they exist so two code paths cannot drift apart.

**One rule, one place.** If a rule is written twice it will drift, and the review that
caught four of these found each copy already meant something slightly different. What a
valid new source is, what re-running a step discards, which source is active, what a
failed step record looks like — each has exactly one implementation, and the browser is
shipped the answer rather than recomputing it.

**Say why, not what.** Most comments here record a Cube behaviour that forced a decision:
why joins are dropped without a primary key, why expressions are `{CUBE}`-qualified but
bare columns are not, why validation restarts a container. Those are the comments worth
writing.

## Adding a warehouse

Two independent pieces, and you can do the first without the second.

**Connecting** — add a `Driver` to `sources/drivers.py` with its `CUBEJS_DB_*` fields.
Field names are Cube's own, so anything Cube documents works verbatim. The source will
connect and serve a hand-written model, and the connection form grows the fields on its
own. Drivers needing a different image or platform set `image` / `platform`; a field
holding a host path (a key file) sets `mount_to`, and the form then renders it as a typed
path — a file input would hand the browser the contents and never the location.

**Extracting tables** — add a `Dialect` to `sources/model/read.py` with SQL returning
`KEY_COLUMNS` (and optionally `STAT_COLUMNS`). Those SELECTs run as probe cubes through
Cube's own driver, so no new database client is needed. `DIALECTS` is the single list of
drivers that can extract tables — the form's `no table extraction yet` label reads from
it, so adding an entry is all that is required.

Statistics are optional. Without them the model still generates; the LLM just has less to
go on when deciding which numbers are worth aggregating.

## Changing model generation

`sources/model/` has one module per stage. `run.action` is the only way to run one — a
caller chooses *which* actions and in what order, never how, so the order rule and what a
failed step records cannot drift across callers. Before changing it, know the three
invariants:

1. **The LLM returns decisions, never YAML.** It picks from a menu derived from the
   database, and every choice is checked against that menu before anything is written. A
   hallucinated join is a silently wrong number.
2. **Cube is the validator.** Candidate models go to the real container and Cube reports
   what is wrong. Do not add a YAML checker — it would be a second, weaker opinion about
   what Cube accepts.
3. **Nothing survives only in memory.** The four steps run minutes or days apart, so each
   writes what the next needs onto the source in `sources.db`. The views step cannot be
   rebuilt from the YAML on disk — it needs the join graph and the cube decisions.

New guards on the model's output belong in `MEASURE_RULES` in `decide.py`: a rule id, a
reason, a predicate. The id shows up in the rejection line, so failures stay countable.

## Pull requests

Say what changed and how you verified it. For anything container- or model-dependent,
include the actual output — the numbers you got and what you compared them against. If
you removed a test, say what still covers the behaviour.
