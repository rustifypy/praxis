# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`CONTEXT.md` is the glossary — what the domain words mean and which synonyms are wrong. Read it
before naming anything. `docs/adr/` holds decisions a reader would otherwise try to "undo" —
`0004` is why the semantic layer is not in this repo and `0005` is why a branch is replayed rather
than merged. This file is how the code fits together.

## Commands

```bash
make install                 # uv sync --all-groups
make test                    # uv run pytest -q  — deterministic only: no containers, no LLM
make lint                    # ruff check + ruff format --check
make fmt                     # ruff format + ruff check --fix

make web-build               # build src/web/ into src/analyst_agent/web/static/ (needs npm)
make web                     # web-build, then serve
make serve                   # uv run analyst — opens the portal in a browser with a token

```

Single test / file / keyword:

```bash
uv run pytest tests/test_gate.py::test_valid_query_passes
uv run pytest tests/test_model_generation.py -q
uv run pytest -q -k dashboard
```

**`__main__.py` is a launcher, not a passthrough.** `analyst` takes no arguments — host, port and
`DATA_DIR` come from `.env`/settings, so `WEB_PORT=8481 uv run analyst` moves it. It cannot be
replaced by `uvicorn analyst_agent.web.server:app`: `web.server.TOKEN` is minted at import and the
launcher is the only thing that prints it, so pointing uvicorn at the app serves a portal nobody
can authenticate against. It prints with `print(..., flush=True)` because stdout is block-buffered
when redirected. `typer`/`rich` are deliberately absent.

**Nothing in the Makefile starts a service, and no service is built here.** The semantic layer
and the sandbox are published images released from their own repositories, brought up by whoever
owns them (`docker compose up -d`). There is no `services/` directory and no test of either in
this suite. What this repo owns is the *clients* (`modelstore/`, `sandbox/`) and the
compose files it renders (`core/deployenv.py`). A second way to manage a deployment is a
second thing that can disagree with its compose file.

Which means **every image in a hand-over is pinned, and the pin is the only compatibility control
there is** — neither service reports a version handshake to check. `deployenv`'s `MODELSTORE_IMAGE`
and `SANDBOX_IMAGE` are the lines to move on an upgrade, and a mismatch surfaces as an error at the
first call, never at startup.

**The frontend build is not committed.** Vite hashes chunk names, so committing `api/static/`
collides on every branch; a fresh clone has no frontend and `analyst` answers 503 until
`make web-build`. It still builds *into* the package because `uv_build` ships only
`src/analyst_agent`. `npm run dev` in `src/web/` gives hot reload against a running `analyst`.

## Layering — the load-bearing rule

Packages are tiered and **a package may import only its own tier or below**. This is not a
convention: `tests/test_layering.py` parses the import graph and asserts it, and a new top-level
package fails the suite until it is given a tier.

**Tier 1 is external integrations, and there are exactly two.** Everything process-local —
settings, the sqlite file *and every table's accessors*, the event format, the LLM builder, the job
runner, the deployment row and the hand-over it renders — is tier 0.

| tier | package | why it sits here |
|---|---|---|
| 0 | `core/` | settings, the sqlite file and the accessors over every table in it (`deployment`, `transcript`, `boards`), the event format, the LLM builder, the job runner, `deployenv`. Imports nothing else in the project — the moment that relaxes, `core` becomes a junk drawer |
| 1 | `modelstore/` | the model store, Cube behind it, the model it serves, and everything that generates or edits that model |
| 1 | `sandbox/` | where the agent runs Python |
| 2 | `agent/` | everything the LLM touches: prompts, the toolsets, one turn |
| 3 | `api/` | HTTP, grouped by what someone is trying to do: `mcp`, `chat`, `chat_management`, `onboarding`, `ide`, `dashboard`. May reach anything; nothing may reach it |

`core.llm.build_model` and `core.events.emit` are what make the rule matter: nearly every package
needs them, so putting either in a feature package would make that feature a dependency of its
peers. `core/llm.py` is an external integration too and stays at tier 0 anyway — it is a builder
rather than a client, and every tier needs it. That exception is named here so nobody "fixes" it.

Three ways a feature is kept from crossing a tier:
- **What a lower tier can't look up, it takes as an argument.** `api/dashboard.controller` is
  *handed* a `Bound` and a `Catalog`; it never opens the deployment row. `editing.impact` is *handed*
  the saved queries it checks against, which is what keeps `modelstore` from knowing dashboards
  exist — `test_layering.FORBIDDEN` is the assertion, and it reads both spellings of an import
  because `from ...core import boards` is how that edge came back the first time.
- **A tool is agent vocabulary wherever the state it edits lives.** All toolsets are in
  `agent/tools/`, importing the stores they write through, and all author their events through
  `tools/trace.py` (one `start`/`end` pair — a second spelling drifts from what the browser matches on).
- **`if TYPE_CHECKING:` is a cycle that hasn't failed yet**, not a fix. Move the thing down a tier
  or pass it in.

**Seven modules at the top of `modelstore/`, and one rule picks them:** the top level is what
asking a question needs (`client`, `catalog`, `schema`, `gate`, `shape`, `charts`) plus what both
subpackages share (`definitions`, because each parses the model's YAML). `modelgen/` builds the
model; `editing/` changes it by hand. Anything that fails that test has exactly one home, and the
imports say which — which is how `drafts` reached `editing/`, `evidence` reached `modelgen/`, and
`serving` left the package altogether for the one route that reads it.

**`modelstore/` is one package because the semantic deployment is one address.** `store_url` serves
the model *and* the queries; the store proxies Cube at `/refs/{ref}/…` and mints its JWT, so a
second client would be a second connection pool and a second error type for one service. The model
it carries bytes for is in the same package for the same reason — the split that had a client at
one tier and the model at another described a deployment that no longer exists.

Two things at tier 0 look like they belong elsewhere and do not. **`core/boards.py`** is the
dashboard rows: accessors over four tables `core.db.SCHEMA` already owns, plus geometry that reads
nothing but `Tile`. They cannot go to `api/` however much a dashboard looks like a page — the agent
creates boards, adds tiles and saves layouts, and `agent` sits below `web`. Same argument for
`core/transcript.py`: the event log *is* the transcript, and the agent authors it. **`core/deployenv.py`**
renders the compose hand-over for *both* services, so inside `modelstore/` the sandbox's deployment
file would be generated from within the model store's package.

**`api/onboarding/reachable.py` is in `api/` although it asks about both integrations.** It speaks HTTP itself
rather than borrowing either client, because it is asked about an address that is *not on the row
yet* and a service that may not exist. `connection.py` is its only caller and renders both answers
into one panel, so the two halves must classify a timeout, a refusal and a wrong service
*identically* — which is the pressure a split would work against. See `docs/adr/0001`.

`modelstore/editing/drafts.py` is not a client at all — it imports only `core`, and a module's home
is what it imports. It deliberately knows nothing about what a model file *may* be: that `cubes/*.yml` and
`views/*.yml` are the only legal paths is `workspace`'s rule, kept there so the agent and the routes
are refused in the same words.

## The subsystems

### Query time — `modelstore/`, `agent/`
The agent answers questions about the one connected database and **never writes SQL**. It emits
Cube JSON queries that pass a four-tier gate:

| tier | what | cost |
|---|---|---|
| 1 | pydantic schema (`modelstore/schema.py`) | free |
| 2 | member existence, operator/type match, WHERE-vs-HAVING, granularity, scope (`modelstore/gate.py`) | free |
| 3 | `POST /refs/{ref}/sql` — Cube compiles to SQL, executes nothing | 1 HTTP call |
| 4 | `POST /refs/{ref}/query` — execute with row/time caps | warehouse |

Failures return as **text the agent can act on, never exceptions** — tier-2 rejections carry the
nearest matching member names. `gate.run` is all four tiers (what a chat turn wants); `check`/
`execute` split at the 3/4 seam for dashboard paths that need to know a query is sound *without*
running it. `execute` takes the `GateResult`, not the query, so the SQL/warnings survive instead of
being recompiled.

Cube is reached **through the model store**, at `/refs/{ref}/{query,sql,meta}` — the ref is the
route, and the store mints the JWT for it, so nothing on this side handles a Cube token.
`deployment.store_url` is the only address and `ModelStore.for_deployment` the only thing that
builds a session; `open_store` re-reads the row for callers too deep to be handed one. The
transport is **ref-parameterised** and `store.at(ref)` returns a `Bound` — a store and one ref —
which is what everything that queries takes, because a chat, a dashboard and a candidate being
compiled are each pinned to one ref for their whole life. `workspace.stage` is why it is that way
round: it reads main and the edited ref in the same call. A question chat and a dashboard are
pinned to main.

Do not migrate the agent to writing SQL without deciding what happens to the gate: tier 2 can check
operators and granularities precisely *because* it inspects a `CubeQuery`, and none of that survives
parsing SQL. The expressiveness gap (`CubeQuery` is `extra="forbid"`, no `ungrouped`, so a ratio or
period-delta can't be expressed) is closed downstream — post-processing rows in the **sandbox**,
not by widening the gate.

### Model generation — `modelstore/modelgen/`
Builds the Cube data model a source exposes. `modelgen` and not `model` because "model" is also the
LLM. Four stages, three step rows: `read` → `cubes` → `describe` → `views`, each runnable and
re-runnable alone. `read` caches a catalog rather than writing model files, so it records no step
row; `cubes` runs it if nothing is cached. There is deliberately **no `connect` step** — connecting
is a form, a step records that something *ran*.

Three invariants: **the LLM returns decisions, never YAML** (pydantic objects via
`with_structured_output`, checked against a derived menu); **Cube is the validator** — a candidate
model goes to the real Cube and it says what's wrong, so do not write a YAML checker; **nothing
survives only in memory** — steps run minutes or days apart, each writes what the next needs onto
the `deployment` row.

**`steps.action` is the only way to run a stage** (a caller chooses which and in what order, never
how), and **`publish.install` is the only way a model reaches Cube — non-destructively**: delete the
leftover candidate → create `candidate` from the target ref → replace the whole model → `check` at
`ref=candidate` → merge or leave it. One Cube compiles every ref independently, so a model Cube
refuses stays readable at `candidate` and nothing serving moves until it compiles. This replaced
unlinking every `*.yml` in the served model — the single worst trap the codebase used to have.

### Editing on a ref — `modelstore/editing/`, `agent/tools/editor.py`, `api/ide/`
Changing a model by hand on a ref of its own, then merging to main.

**A ref being edited has a draft on this machine, and the draft is the current model.**
`editing/drafts.py` holds two trees per ref — `origin/` as the store last handed it over, `model/` as it
is being edited — plus `origin.json` carrying the store revision they came from. That is what
makes "what have I changed" and "put it back" answerable without asking the store, and what makes
staleness a `rev` comparison rather than a guess. **There is still no git here**: no repository, no
subprocess, no history, no remote. Branching, committing and merging are things the portal *asks
the store to do* over HTTP, and a draft is a working copy, never a second source of truth — see
`docs/adr/0003`, which this supersedes.

`workspace.py` is the one door for the HTTP routes and the editor tools, with three
non-interchangeable entry points: `stage` (tiers 1-2, writes nothing), `verify` (tiers 1-3 plus
impact on saved queries, on a throwaway ref it then deletes — this is Save), `publish` (tiers 1-2
then one commit at the store — this *is* the deploy). `stage`'s `write`/`delete` are an *overlay*
on the draft, not a change set: the only caller passing them is the agent gating a proposal it has
not written anywhere. `workspace.ensure` checks a ref out on first touch, so every entry point is
self-healing.

Landing a branch is `workspace.land`: fast-forward, else rebase, else **replay** the ref onto a
fresh branch off main. The store fast-forwards only and main is generated, so a rebase conflicts
on files nobody touched — see `docs/adr/0005`. It refuses a branch whose draft is unpublished.

**Everything from `verify` on is set-shaped**: a change spanning two files can't compile one at a
time, so a verdict covers the whole draft and one commit lands it. Cubes are **append-only**
(add/edit descriptions, never rename or remove); views are freely rewritable, diffed against
**main**. Failures are an `Outcome`/`Verdict`, never an exception. Main is generated, never edited
— `workspace.editable` refuses it, and the store refuses a write to its prod ref too.

The store takes **one file per request and commits separately**, and has no bulk read; the client
hides both. So `ModelStore.edit` is deletes, then writes, then one commit — deletes first because
a model missing a file compiles and one carrying a stale file beside its replacement does not —
and it skips the commit when every write reported `changed: false`, since the store 409s an empty
one. Merge is **fast-forward only**, into main and nowhere else.

### Dashboards — `core/boards.py`, `modelstore/charts.py`, `api/dashboard/`
The same machinery pointed at a repeated question. A tile stores the **query, not the rows**. Read
`api/dashboard/__init__.py` before touching this; its three invariants: **the row is the truth and a
tool event carries only intent** (undo comes from `layouts` being append-only, not from state on the
event); **a refresh is atomic** (every tile compiles before any executes, and the same pass runs on
open); **a refresh re-executes, never rewrites** (whether a window moves is a property of the stored
query — Cube resolves `"last 30 days"` at execution time). **The agent authors and arranges; the
page runs** — refresh is a button, because an agent that could trigger one turns "tidy up this
board" into a dozen warehouse queries. A just-added tile has no data until someone refreshes, and the
prompt says so. `charts.CHART_TYPES` is exactly what `ResultChart` draws — widen the renderer branch
*before* the tuple. It sits in `modelstore/` beside the renderer limits it has to agree with,
because "does this chart fit these columns" is a question about a query and never about a row.

### Code execution — `sandbox/` (client), `agent/tools/code.py`
A Python sandbox on the *query* agent, not an agent of its own. Read `sandbox/__init__.py` first.
**What keeps the gate load-bearing is that the sandbox holds no credential** — `run_query` and the
interpreter are in the same hands, safe because the sandbox can't sign a Cube token and has no
warehouse password. `export_result` is the only way rows cross over. This buys the arithmetic a
`CubeQuery` can't express (ratios, period deltas, shares). `--network none` is gone with the move to
a service — the accepted price of the portal running nothing. A script is shown before it runs
(`approve_scripts`); the approval carries the script body and is the one event with a body. Stopping
asks the service to kill the process *group*; it does not cancel the stream.

### Web portal — `web/`, `src/web/`, `core/transcript.py`, `core/events.py`
`analyst` opens a browser Jupyter-style. **One flat event stream is the contract** between agent and
browser. Two invariants: **the event log is the transcript** — `Session` provenance is rebuilt from
it (`agent.session.replay`), so nothing lives only in memory; and **tools author their own events**
via `agent/tools/trace.py`, so a gate rejection reaches the browser with its reasons. The one
exception is `output` (a running script's stdout, live-only — the whole of it lands trimmed on
`tool_end`); check a type is already stored elsewhere before adding another wire-only one. Both
agents share every seam: `run_turn`, `api.streamTurn`, `events.applyEvent`, `Transcript.tsx` — each
was two copies once and the copy always dropped something.

**Rows are not in the trace.** `tool_end` holds `rows[:MAX_POINTS]` plus a `result_id`; the full set
is its own transcript block from the `results` table. Header and chart are pure functions of (query,
catalog, rows) in `modelstore/shape.py` / `modelstore/charts.py` — derived, never asked for; `chart_for`
returning `None` (no chart) is a normal, correct answer.

**The HTTP surface is six product areas**, each a package under `api/` and a prefix on the wire:
`/api/onboarding` (connect → hand over → build), `/api/ide` (the model and its branches),
`/api/chats` (a chat in use), `/api/dashboard`, `/api/mcp`. A seventh prefix is a claim there is a
seventh thing someone is trying to do.

Two of the six do not get a prefix of their own, for reasons worth keeping. `chat_management`
shares `/api/chats` with `chat` — making and naming a chat is a different product surface from
using one, but it is the same noun, and nothing in it goes deeper than `/{chat_id}` so neither can
shadow the other. And a result stays at `/api/results/{id}`: it belongs to a chat, but the browser
addresses it by id alone from a `tool_end` event, so it is a permalink rather than a sub-resource.

`onboarding` runs two routers because `POST /pipeline/{stage}` is as broad as a path parameter
gets — under a shared `/api/onboarding` prefix it would have answered for `/connection`. **"Catalog" appears nowhere** — the name means
`modelstore.catalog.Catalog` (the served model), and the warehouse's tables are
`/api/onboarding/pipeline/tables`.

The browser is separate products, so separate modes (`Rail.tsx` — chat / boards / data / agents),
read off the hash and nowhere else.

## The deployment — the portal runs nothing

The portal holds credentials, mints secrets, renders a compose file per service, and is told where
things are. It starts nothing — not Cube, the store, the sandbox, or a probe — so it can never
observe a stop. **Two deployments, sharing nothing:**

- **The semantic layer** — `modelstore`, `cube-api`, `cubestore`, all published images. **Cube's
  port is not published**; the store serves it at `/refs/{ref}/query`, minting a JWT that names a
  ref *and* a revision. One authenticated door; `CUBEJS_API_SECRET` never leaves that compose file.
  Cube runs its **stock** image with `cube.py` supplied through compose `configs`, so there is no
  image of ours to build. `CUBEJS_DEV_MODE` is false and there is no second Cube: dev mode
  disables JWT verification, and upstream runs it only for warehouse introspection and pre-agg
  building — this portal reads the warehouse through the production Cube and builds no
  pre-aggregations.
- **The sandbox** — where the agent runs Python. Optional to the code, required by onboarding.
  Its own published image, sharing nothing with the semantic layer: no network, no volume, no
  secret. `SANDBOX_EXEC_TIMEOUT_S` and `SANDBOX_PROFILE_TIMEOUT_S` are rendered into its file
  rather than left at the image's defaults, so `sandbox.client`'s caps can be held above them
  from this side — `test_timeouts` is what holds both pairs in order.

**One deployment, one row** in `deployment` (`id = 1` is a CHECK). `core/deployenv.py` is the
one place that decides what a deployment needs and **generates** the hand-over — no `.env`, nothing
to disk. The templates are inlined there (`SEMANTIC`/`SANDBOX`) because `uv_build` ships only
`src/analyst_agent` and a wheel must be able to render a hand-over. `SEMANTIC` is now the *only*
copy here, with the store's own `compose.yml` as its reference — they can drift, accepted
deliberately, and `MODELSTORE_IMAGE` is the line to move when the store is upgraded. Pinning is
the only compatibility control there is: **the store reports no contract**, so `CONTRACT` is the
sandbox's alone and `reachable.semantic` checks none.
`GET /api/onboarding/deployment/{service}/compose` is the only route that returns credential *values* (that is
its whole purpose) and answers `Content-Disposition: attachment`.

Connecting is **two steps** because the portal can't do the middle one: `POST /connection` stores
credentials and mints secrets with no addresses → someone runs the compose file → `PUT /deployment`
receives `store_url` and `sandbox_url` and checks each before storing. Four conditions, and
conflating them is the mistake the glossary exists to prevent: `deployed` (an address is stored) →
`onboarded` (both are, plus a model) → *reachable* (something answers and it's the right thing) →
*serving* (Cube is compiling a model). Only the first two are properties of the row; the other two
cost a request. `api/onboarding/reachable.py` holds both reachability questions — see `docs/adr/0001`.

## Storage — one file, one schema

`core.db.SCHEMA` is every table this process creates and `~/.analyst/analyst.db` holds all of them:
`chats`/`events` (the transcript), `results`, the dashboard tables, `deployment`, and LangGraph's
`checkpoints`/`writes`. `core.db.connect` is the one opener, so `PRAGMA foreign_keys` can't drift.
**The accessors live beside the schema**, one module per group: `deployment.py`, `transcript.py`,
`boards.py`. That is the rule that keeps `core` from being a junk drawer despite its size — a module
belongs here when it needs nothing from above, and a table's accessors never do.
The file is **chmod 600** (`DeploymentStore.init` sets it) because `deployment.credentials` is
plaintext JSON with the warehouse password. `DATA_DIR` scopes the whole install, connection
included — a scratch dir is a scratch everything.

**There is no migration mechanism, deliberately.** `CREATE TABLE IF NOT EXISTS` does not diff, so a
new *table* appears on an existing db and a new *column* silently does not; changing a column means
deleting the file. Affordable for one machine, the first thing to revisit if this ever runs shared.

**A file is not a row.** An uploaded CSV, a script's chart and a file's profile live in the
**sandbox service's** volume, one directory per chat, with no table — a row would only ever be a
stale copy of `ls`.

## Conventions

- **Tiers before packages.** A module's home is what it imports, not what it's about. `test_layering`
  will not let it import upward, and `FORBIDDEN` there covers the one edge a tier is too coarse to
  express: `modelstore` may not name `core.boards`.
- **A name is taken once.** `modelstore/` holds `gate.check`, `edits.check`, `impact.check` and
  `publish.check`. All four are qualified at every call site and none is ambiguous in context, but a
  fifth `check` in that package should be refused. `label_of` used to be taken twice here — the
  deployment's name and a column header — and the first is now `Deployment.label`.
- **`catalog` is the served model and nothing else.** The warehouse's tables are an
  `Introspection`: `modelgen.read.introspect` produces one, `steps.cached_introspection` decodes
  the cached one, and the deployment row carries it under `introspection`. The two meanings lived
  in one package until they were separated, and the confusable pair was `catalog` beside `schema`.
- **A name is taken once.** `Catalog`/`Entity`/`MemberDoc`/`Folder` mean the *served* model
  (`modelstore.catalog`); the generation side is `Introspection`/`Definition`/`MemberDescription`/
  `FolderDecision`. Grep before naming.
- **One rule, one place.** What re-running a step discards, what a failed step looks like, layout
  validation — each lives once and is shipped to the browser, never recomputed there.
- **No single-caller private helpers** — inline them; procedural flow reads better than one-use hops.
- **One `try` per function** where practical — one `try` at the top, `except` clauses at the end.
- Tests cover the deterministic half only. Anything container- or LLM-dependent is verified by
  running it against a live source, not mocked.
- Comments explain *why*, especially where a Cube behaviour forced the design. Do not describe
  removed architecture in the present tense — deletion rationale goes in the commit message.
- Ruff, line length 100, `select = ["E", "F", "I", "UP", "B"]`.

## Cube behaviours that forced the design

These cost real hours and are not guessable from the docs:

- **Cube compiles every `*.yml` anywhere under the model mount**, not just `cubes/`/`views/`. One
  stray YAML file elsewhere in the model takes it to zero cubes, so a path outside those two
  directories is refused rather than written.
- **A model change is live within seconds, with no restart.** `cube.py` keys Cube's compiled-model
  cache on `<ref>@<rev>` and reads that revision's immutable snapshot, and the store mints the JWT
  carrying both — so a new revision is a cache miss and a recompile. This is *why* the edit gate
  runs before the write: there is no restart afterward to hide behind.
- **Cube qualifies a single-identifier `sql:` but passes an expression through verbatim** — so
  `sql: quantity * unit_price` dies with an ambiguous-column error once a join is involved.
  `decide.qualify_expression` qualifies multi-column expressions with `{CUBE}`.
- **`CUBEJS_DEV_MODE=true` disables JWT verification** and is what serves `/playground/db-schema` —
  so nothing reads that endpoint; catalog reads come from `information_schema` instead, which works
  against a production-mode Cube on someone else's machine.
- **Production Cube refuses to query without Cube Store** (`/v1/meta` answers, every `/v1/load`
  fails), which is why the compose file ships three services.
- **Cube Store publishes one architecture per tag, with no multi-arch manifest** — `latest` is
  amd64, `arm64v8` is arm64, and the wrong one fails at `up` with "no matching manifest" instead
  of falling back. `deployenv.CUBESTORE_IMAGE` picks it from the portal's own `platform.machine()`;
  `deployenv.CUBESTORE_IMAGE` picks it from the portal's own `platform.machine()`. Neither can be
  right for both hosts at once, and the rendered file's comment says which line to change.
- **A grouped time dimension comes back under two keys** (`sales.invoiced_at` and
  `…invoiced_at.month`), so a result header is computed by `shape.columns_of`, never
  `Object.keys(rows[0])`.
- **`httpx` transport errors do not subclass `OSError`.** The established catch pair is
  `(StoreError, httpx.RequestError)`; grep for an existing except-clause before writing one.
  `StoreError.where` says which half of the one address failed — `store` or `cube`.
- **A stored `dateRange` is not a window** — Cube resolves it at execution time, so `"last 30 days"`
  moves and `["2026-01-01","2026-01-31"]` does not, and nothing in the stored query distinguishes
  them until `LoadResult.resolved_ranges` reads what Cube echoed back.

## LLM / infra gotchas

- **Reasoning summaries need Azure `AZURE_OPENAI_API_VERSION=2025-03-01-preview` or later**;
  `analyst` refuses to start below it. `REASONING_SUMMARY=detailed` is what actually fills the panel
  (`"auto"` frequently emits nothing).
- **The Responses API makes `.content` a list of blocks, not a str.** Never read it directly —
  `core/llm.py` (`text_of`, `reasoning_of`) is the only correct accessor.
- **`pydantic-settings` reads `.env` into `Settings` and puts nothing in `os.environ`**, so SDKs
  looking up their own env vars find nothing; `build_model` passes `api_key` explicitly.
- **`get_stream_writer()` raises outside a runnable context** (how unit tests invoke a tool
  directly); `core.events.emit` swallows exactly that `RuntimeError` into a no-op.
- **Event-assertion tests monkeypatch `emit`**, so the real no-op path and serialization bugs
  (`asdict` on a pydantic `ChartSpec`) go uncovered — `test_query_tools.py` / `test_code_tools.py`
  drive tools with `emit` left alone for that reason. When a fixture replaces the thing under test,
  add one case without it.
- **`python-multipart` is a separate dependency**; without it `UploadFile` fails at *request* time,
  not import — the upload route looks fine until the first file.
- **Compose interpolates `$` *after* the YAML is parsed**, so every credential value in the
  hand-over is written JSON-quoted with `$` doubled (`deployenv.semantic`). Unquoted, a pasted
  service-account key parses as a YAML flow mapping and a certificate is not a scalar at all;
  un-doubled, a password containing `$` arrives truncated. This is also why `render`'s
  unresolved-variable guard skips the credentials block.
- **A warehouse's settings are not all `CUBEJS_DB_*`** — Athena's are `CUBEJS_AWS_*`, which is why
  `connection.ENV_PREFIXES` is a tuple. Which settings each warehouse needs lives twice, on
  purpose: `deployenv.REQUIRED` refuses, and `AddSource.tsx`'s table draws the form.
