# Contributing

Thanks for considering it. This project follows the standard GitHub flow: fork, branch,
PR against `main`. Everyone participating is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Contribution flow

1. Fork the repo and clone your fork.
2. Create a branch off `main`: `git checkout -b fix/short-description`.
3. Make your change. Keep it scoped — a bug fix doesn't need surrounding cleanup, and a
   PR that mixes an unrelated refactor with the fix is harder to review and harder to
   revert.
4. Run the loop below (`make test`, `make lint`) and, for anything container- or
   model-dependent, verify it against a real source (see below) — the test suite can't.
5. Open a PR against `main` on this repo. The template asks what changed, how you
   verified it, and whether docs need updating; fill it in rather than deleting it.
6. CI runs `make lint` and `make test` on every PR. A maintainer reviews from there.

Small, focused PRs get reviewed faster than large ones. If you're planning something
bigger than a bug fix, open an issue first so the approach can be discussed before you've
written the code — especially for anything touching an invariant documented in
`AGENTS.md`.

## Setup

```bash
cp .env.example .env      # Azure OpenAI key + endpoint; check QUERY_MODEL
make install              # uv sync --all-groups
```

You need a database of your own — nothing is bundled. Point the tool at it from the portal:
`make web`, then **Data** in the rail, **Connect a database**.

That writes one row to the `deployment` table in `~/.praxis/praxis.db` — credentials, the
API secret and the model store's token, chmod 600 — and **starts nothing**. The portal does not
run Cube. It renders a compose file from that row; you download it, run `docker compose up -d`
wherever it should live, and paste the two addresses back. Connecting is two steps because the
portal cannot do the middle one.

Docker is needed only for the **code sandbox** and for running that compose file locally. A
machine without it can still connect, build a model and ask questions — it just cannot run
Python.

The portal is the only surface for this — `POST /api/onboarding/connection` is the sole writer of
that row, and `api/onboarding/` the sole caller of `steps.action`. Keep it that way: a second surface means
the connection form, its validation and the pipeline's confirmations get written twice, which is
exactly how two implementations of one rule start disagreeing.

A database running on your machine is `host.docker.internal` from inside the container, not
`localhost`. On Apple Silicon edit the handed-over file to `cubejs/cubestore:arm64v8` —
`cubejs/cubestore:latest` is amd64 only and the pull fails outright rather than falling back.
The file's own header comment says so; the portal no longer picks the tag.

## The loop

```bash
make test                 # 545 tests, no containers, no model calls
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

**Re-running the `cubes` step discards the views and descriptions**, and those cost one model
call per table to rebuild. It can no longer lose them: `publish.install` replaces the whole
model in one commit on a ref, so what was there is the parent commit and `GET /diff` shows what
the rebuild changed. The picker still warns first, because recoverable is not the same as
intended.

## Code style

Ruff, line length 100. Beyond that, two habits this codebase holds to:

**Inline single-caller helpers.** A private function with one call site is a hop the
reader has to follow for no benefit. The pipeline in `modelstore/modelgen/` is procedural on
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

**Connecting** — nothing. The connection form takes arbitrary `CUBEJS_DB_*` variables and
`POST /api/onboarding/connection` checks only that every name carries that prefix, so a warehouse this
repo has never heard of connects and serves a hand-written model with no code at all. There is
no per-driver field table any more: `WAREHOUSES` in `ConnectionForm` is a dropdown with a
starting set of rows for each entry, and it is prefill rather than schema — nothing validates
against it and a type missing from it is typed in and works.

A warehouse needing a different Cube build or an architecture pin (Databricks wants
`cubejs/cube:<version>-jdk` and `platform: linux/amd64`) is an edit to the compose file the user
was handed. The portal has no opinion: the templates in `deployenv` carry pinned images, the same
way they carry static ports.

**Extracting tables** — add a `Dialect` to `modelstore/modelgen/read.py` with SQL returning
`KEY_COLUMNS` (and optionally `STAT_COLUMNS`). Those SELECTs run as probe cubes through
Cube's own driver, so no new database client is needed. `DIALECTS` is the single list of
drivers that can extract tables; a driver missing from it still gets tables, columns and types
from `information_schema`, and `read` records a note saying its cubes will have no joins and no
primary keys.

Statistics are optional. Without them the model still generates; the LLM just has less to
go on when deciding which numbers are worth aggregating.

## Changing model generation

`modelstore/modelgen/` has one module per stage. `steps.action` is the only way to run one — a
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
   writes what the next needs onto the source's row. The views step cannot be
   rebuilt from the YAML on disk — it needs the join graph and the cube decisions.

New guards on the model's output belong in `MEASURE_RULES` in `decide.py`: a rule id, a
reason, a predicate. The id shows up in the rejection line, so failures stay countable.

## Changing the model editor

`modelstore/editing/{edits,impact,workspace}.py`, `agent/tools/editor.py` and `api/ide/`
let a ref of the model be edited safely. There is no local model directory and no git
subprocess: the store does refs, diff and merge over HTTP, and one Cube compiles every ref
independently, so a ref is not switched *to* — it is asked *about*. Two invariants hold before
you touch it:

1. **The gate runs before the write, because the write is the deploy.** `edits.check` is what
   keeps a file that will not compile from ever being committed — Cube asks the store for the
   ref's version on every request, so a commit is live within seconds and there is no restart
   afterwards to hide behind. A new write path that skips it can take a ref to zero cubes.
2. **Impact is measured on the ref being asked about, and every ref is answerable.** This is
   what one-Cube-per-ref bought: `GET /refs/{ref}/diff` reports real breakage for a ref nobody
   is looking at, and merge no longer has to demand a checkout first. `impact.check` asks the
   ref's own compiled catalog rather than diffing members against main, because "is what this
   tile reads still here" is the question that survives whatever caused the breakage.

The editor agent's tools (`agent/tools/editor.py`) deliberately exclude merge and ref-create —
those land work, so they stay buttons on the page rather than something an agent can trigger
mid-conversation. There is no commit tool either: every write to a ref is already a commit.

## Pull requests

Say what changed and how you verified it. For anything container- or model-dependent,
include the actual output — the numbers you got and what you compared them against. If
you removed a test, say what still covers the behaviour.
