# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`CONTEXT.md` is the glossary — what the domain words mean and which synonyms are wrong. This file
is how the code works. Decisions that a reader would otherwise try to "fix" are in `docs/adr/`.

## Commands

```bash
make install                  # uv sync --all-groups
make test                     # uv run pytest -q — no containers, no model calls
make lint                     # ruff check + ruff format --check
make fmt                      # ruff format + ruff check --fix

make store-test               # the model store's own suite (31 tests)
make sandbox-test             # the sandbox service's own suite (19 tests)
make store-check              # live: the store against a real Cube — two refs, fail-soft

uv run pytest tests/test_gate.py::test_valid_query_passes    # a single test
uv run pytest tests/test_model_generation.py -q              # one file
uv run pytest -q -k dashboard                                # by name

make web-build                # build the frontend into the package (needs npm)
make web                      # web-build, then serve
make serve                    # analyst — opens the portal in a browser
```

**Nothing in the Makefile runs a service.** `services/semantic/` and `services/sandbox/` are brought up by
whoever owns them, with `docker compose up -d`. There is deliberately no target that starts or
builds either: a second way to manage a deployment is a second thing that can disagree with its
compose file, and the whole point of them being services is that this repo does not run them.
`make store-check` is the exception that isn't one — it stands up a throwaway world of its own,
builds both images itself if they are missing, and touches nothing of yours.

**`__main__.py` is a launcher** — a printed URL and `uvicorn.run`, and **it takes no
arguments**: host, port and data directory come from settings, so `WEB_PORT=8481 uv run
analyst` is how you move it and `.env` is where it lives. `analyst`
starts the portal and nothing else. **The portal is the only surface**: add capability to
`web/connection.py` and the panel, never a second entry point beside it. Two of them means the
connection form and the pipeline's confirmations get written twice. Anything headless
belongs behind the portal's token guard, not beside it.

`typer` and `rich` are deliberately absent from the dependency list — nothing here needs
either. That is why the launcher prints with `print(..., flush=True)` rather than
`console.print`: stdout is block-buffered when it is not a tty, so a redirected log would
hold the token in the buffer for the life of the server.

The launcher cannot be replaced by pointing uvicorn at the app. `web.server.TOKEN` is
minted at import and nothing else announces it, so `uvicorn analyst_agent.web.server:app`
serves a portal nobody can authenticate against.

## Layering

Packages are tiered, and **a package may import only its own tier or below**.
`tests/test_layering.py` parses the imports and asserts it; it is not a convention.

| tier | package | may reach |
|---|---|---|
| 0 | `core/` | nothing in this project |
| 1 | `cube/`, `modelstore/`, `sandbox/`, `reachable.py`, `transcript.py` | core |
| 2 | `dashboards/` | core, cube |
| 3 | `datamodel/` | core, cube, dashboards |
| 4 | `agent/` | core, cube, transcript, dashboards, datamodel |
| 5 | `web/` | anything |

`core/` is settings, the database file, the event format, the chat model, the deployment row
and the job runner — **and it is a junk drawer the moment that rule is relaxed**, so the
membership test is exactly "imports nothing from this project except its siblings."

`core.llm.build_model` and `core.events.emit` are the two that make the rule load-bearing:
nearly everything needs them, so whichever package holds them is one every other package
must depend on. Put either in a feature package and that feature is imported by its peers.

**`cube/` and `modelstore/` are one tier because they are one deployment.** The semantic layer is
one address, `store_url`, serving the model *and* the queries — and these are the two clients on
it, each importing nothing but `core.deployment`. Neither belongs inside `datamodel/`, however
much "the model store holds the data model" suggests it: `datamodel/` reaches `cube` and
`dashboards`, so a client living there could import the semantic layer it is only supposed to
carry bytes for. Both are also used from tiers 3, 4 and 5, and a thing three tiers reach for
sits at the lowest tier that satisfies it or the lowest of them imports upward.

**`reachable.py` is tier 1 and speaks HTTP itself rather than borrowing a client**, which looks
like duplication and is not: it is asked about an address that is *not on the row yet*, and about
a service that may not be there at all, so there is nothing to build a client on. It is what put
`CONTRACT` and `CUBE_PREFIX` in `core/deployment.py` — the handshake number and the proxy path
are facts about what a deployment is, and `reachable` composes `{store_url}{CUBE_PREFIX}/readyz`
while `cube/client.py` composes `{store_url}{CUBE_PREFIX}{cube_base_path}`, so the constant is
shared by two modules that build different paths out of it. `deployenv.CONTRACT` is gone as a
name; the tag and the handshake were always one number and now have one home.

Three rules keep the tiers honest when a feature wants to cross one:

- **A tool is agent vocabulary wherever the state it edits lives.** All four toolsets are in
  `agent/tools/`, importing the stores they write through, and all four author their events
  through `tools/trace.py` — one `start`/`end` pair, because three spellings of it is how the
  shape a browser matches on drifts.
- **What a lower tier cannot look up, it takes as an argument.** `dashboards.refresh` is
  handed a `CubeClient` and a `Catalog`; it never opens the deployment row. That is the whole
  reason `dashboards` and `datamodel` are orderable at all.
- **An import inside `if TYPE_CHECKING:` is a cycle that has not failed yet**, not a
  solution. Move the thing down a tier, or pass it in.

## Six subsystems

**Query time** (`cube/`, `agent/`, `datamodel/grounding.py`) — the agent answers questions
about the one connected database. Cube is reached **through the model store**, which proxies it
at `/cube/...` and mints its JWT — so `deployment.store_url` is the only address and
`CubeClient.for_deployment` is the only thing that builds a session on it; `open_client` reads
the row fresh for the callers too deep to be handed one. It never writes SQL. It emits
Cube JSON queries that pass a four-tier gate (pydantic schema → local semantic checks against the cached catalog → `POST /v1/sql`
which compiles without executing → `POST /v1/load`). Failures return as text the agent
can act on, never exceptions. See README for the tier table.

`gate.run` is all four tiers and is what a chat turn wants. The seam between 3 and 4 is
also exposed as `check`/`execute`, for the callers that need to know a query is sound
*without* running it — both are dashboard paths. `execute` takes the `GateResult` rather
than the query, so the SQL and warnings tiers 2-3 produced survive into the executed
result instead of being compiled twice.

Cube itself moved off this format — the 2024 AI API emitted Cube JSON and D3 replaced it
with the agent writing Semantic SQL, for the expressiveness a fixed schema cannot give.
Do not follow them without deciding what happens to the gate: tier 2 can check operators,
granularities and WHERE/HAVING mixing precisely *because* it is inspecting a `CubeQuery`,
and none of it survives parsing SQL. The expressiveness gap is real — `CubeQuery` is
`extra="forbid"` with no `ungrouped`, so a ratio or a period delta cannot be expressed. That gap
is closed the way it should be: post-processing over rows that already passed the gate, in the
sandbox, rather than by widening what the gate inspects. See **Code execution** below.

Grounding lives **beside the cubes it names**, at
`grounding/{rules,skills,certified_queries}/*.md` **in the model store** — so it is versioned
with the model it describes and a ref carries its own. `grounding.load` reads it through
`modelstore.model_files`, so a ref that does not exist grounds on nothing rather than erroring,
which is what `Grounding.is_empty` is for.

**Model generation** (`datamodel/modelgen/`) — builds the Cube data model a source exposes.
`modelgen` and not `model`, because "model" in this codebase is also the LLM:

| module | stage |
|---|---|
| `warehouse.py` | the vocabulary: `Column`, `Table`, `Schema` — and `dump_schema`/`load_schema`, which are what one step leaves for the next. They live beside the type they serialise rather than in a `state.py`, because `web/state.py` already owns that name |
| `read.py` | probe ref → `Introspection` (every schema: keys, columns, statistics). Not `Catalog` — that name is `cube.catalog.Catalog`, the *served* model, which is close to the opposite thing |
| `selection.py` | which tables to model, as a filter on one `Schema` |
| `build.py` | `Schema` → cube bodies + the menu of choices (pure, no LLM) |
| `decide.py` | LLM descriptions/measures/views, and the guards on them |
| `publish.py` | decisions → YAML, then commit to a candidate ref and ask Cube about it |
| `state.py` | what one step leaves for the next, as JSON |
| `steps.py` | the three steps plus `read`, and `generate()` for all of them in order |

Three invariants hold throughout: **the LLM returns decisions, never YAML** (pydantic
objects via `with_structured_output`, then checked against a derived menu); **Cube is the
validator** — a candidate model goes to the real Cube and it says what is wrong, so do not
write a YAML checker; and **nothing survives only in memory** — the steps run minutes or days
apart, so each writes what the next needs into the `deployment` row.

**`publish.install` is the only way a model reaches Cube, and it is non-destructive.** Delete
the leftover candidate → create `candidate` from the target ref → `replace` the whole model on
it → `check` at `ref=candidate` → merge, or leave it. One Cube compiles every ref independently,
so a candidate is validated with **no restart** and nothing that answers a query moves until it
has compiled. A model Cube refuses stays readable at `candidate`, which `Validation.candidate`
carries so `steps._rejected` can say where. This is what replaced unlinking every `*.yml` in the
served model and writing the new one over it.

The candidate is deleted and recreated rather than committed onto, because one that has diverged
would make the merge a real three-way one — which is how a stale side comes quietly back. The
cleanup delete after a successful merge is best-effort: the model has landed, and raising there
would report a rebuild that worked as a step that failed.

**Four stages, three steps.** `read` → `cubes` → `describe` → `views`, each runnable on its own
and re-runnable. **`steps.action` is the only way to run one** — a caller chooses *which*
actions and in what order, never how, so nothing can drift on the order rule (a step needs the
previous one done) or on what a failed step records. `read` is the odd one: it caches a catalog
rather than writing model files, so it records no step row, has to be triggerable alone, and
`cubes` runs it itself when nothing is cached. There is **no `connect` step** — connecting is a
form, and a step records that something once succeeded while a connection is a condition.

Nothing in `action` commits or rolls back. `publish.install` commits either way, so a step that
fails leaves the served model exactly as it was and there is nothing to arrange.

`selection` is why nothing downstream knows selection happened: `build` already tolerates
a foreign key whose target is absent (it was written that way for keys into another
schema), and a deselected table is indistinguishable from that.

**Editing on a ref** (`datamodel/editing/{edits,impact,workspace}.py`,
`agent/tools/editor.py`, `web/model.py`,
`src/web/src/components/{ModelEditor,EditorChat}.tsx`) — changing a model by hand, on a ref of
its own, then landing it on main.

**There is no local model directory and no git subprocess.** `datamodel/vcs.py` and `core/proc.py`
are gone: the store does refs, diff and merge over HTTP and `ModelStore` is the whole client.
`registry.model_dir` went with them, because a directory on this machine was served by nothing.

**A bare repo has no working tree, so three concepts have no counterpart and were dropped rather
than emulated**: `status`, `commit` and `discard`, and with them the dirty-files idea, the
`POST /commit` and `POST /discard` routes, the editor's `commit_changes` tool, and the Commit /
Discard / *n* uncommitted controls. **Every write to a ref is already a commit** — `store.edit`
takes the message — so there is nothing to stage and nothing to throw away.

**There is also no checkout.** One Cube compiles every ref independently, so a ref is not
switched *to*; it is asked *about*. The ref selector in `ModelEditor` is client-side state and
touches no route, which is why nothing on the server can disagree with it. The consequence worth
stating: `ref` is a parameter on nearly everything (`CubeClient`, `open_client`,
`served_catalog`, `publish.check`/`install`, `grounding.load`, every `ModelStore` method) and
defaults to `MAIN`. A question chat and a dashboard are pinned to main and always were.

| module | role |
|---|---|
| `edits.py` | tiers 1-2: valid YAML, then append-only cubes vs. freely-rewritable views, diffed against **main**, never against the ref's own previous state. Pure — two dicts of path → text — which is why it survived the move untouched |
| `workspace.py` | `write` and `delete` against a ref, which both the HTTP routes and the editor agent's tools go through — one path, so the two cannot disagree on what a valid edit is. Also `validate_ref` (what a ref may be named) and `editable` (main is not) |
| `impact.py` | every dashboard tile and certified query, checked against a ref's compiled catalog — not a member-diff of main against the ref, because "is what this tile reads still here" is the question that survives whatever caused the breakage |
| `agent/tools/editor.py` | the editor agent's five tools — no merge, ref-create or commit; the first two land work and the third does not exist |
| `web/model.py` | the HTTP surface: what is served, its grounding, refs, files (`?ref=`), validate (tier 3), merge, and the editor chat |

Three invariants:

1. **The gate runs before the write, because the write is the deploy.** Cube asks the store for
   the version of the ref it serves on every request, so a commit is live within seconds —
   `edits.check` is what keeps a file that will not compile from ever being committed.
   `write_model_file` and `PUT /files/{path}` both run the identical two-tier check first, and
   neither has another way to refuse. **There is nothing to restart**: `_reloaded` only clears
   the portal's cached health probe, because that is the only stale thing left.
2. **Impact is measured on the ref being asked about, and every ref is answerable.** This is
   what the move *bought*: the old rule was "only the checked-out branch", because any other
   branch described a model nothing was running. With one Cube per ref, `GET /refs/{ref}/diff`
   reports real breakage for a ref nobody is looking at, and merge no longer has to demand a
   checkout first.
3. **A workspace failure is an `Outcome`, never an exception.** Both callers turn one into a
   status code or into prose for the agent, so a `StoreError` escaping `workspace` would 500 the
   route and kill the turn. `kind` is `ref | path | missing | rejected | store`.

Main is generated, never edited directly — `workspace.editable` refuses a write there, and both
the routes and the tools go through it. A ref only ever moves forward from main: no pull, no
rebase, no remote, because there is nothing to sync with. A merge conflict aborts rather than
leaving markers: the store's `merge-tree --write-tree` has no working tree to leave them in,
which is the same reason a stray `grounding/*.yml` is fatal — conflict markers in a compiled
model are "zero cubes", arrived at from a different direction.

**A path is validated for shape, not resolved against a tree.** `workspace.resolve` rejects an
absolute path, a `..` segment and a dotted segment, and requires `.yml`. The old version
`resolve()`d against `model_dir()` and compared the result, which was correct then and impossible
now. Stripping a leading `/` instead of refusing it is the trap: `/etc/passwd.yml` becomes a
plausible relative path, and the store would resolve it inside the ref.

**Dashboards** (`dashboards/`, `web/dashboards.py`, `src/web/src/components/{Dashboard,
DashboardNav,Tile}.tsx`) — the same machinery pointed at a question someone asks
repeatedly. A tile stores the *query*, not the rows. `dashboards/__init__.py` holds the
three invariants; read it before changing anything here:

1. **The row is the truth, and a tool event carries only intent.** A dashboard is mutable
   state and the chat's contract is that the event log *is* the transcript; both cannot
   be true of a layout the agent edits. So `tools.trace.start`/`end` are the only way these
   tools reach the stream, and `EVENT_KEYS` is what says a layout, a tile list or a chart spec
   may not be added to one — that is the enforcement, not a rule to remember. Undo comes from `layouts` being append-only instead.
2. **A refresh is atomic** — every tile compiles before any tile executes, which is what
   `gate.check`/`execute` exist for. The cost is that three dead tiles break the whole
   board, so the same compile pass runs when a dashboard *opens* (`web/dashboards._checks`)
   and reports a member a model rebuild dropped as a banner.
3. **A refresh re-executes; it does not rewrite.** Whether the window moves is a property
   of the stored query — Cube resolves `"last 30 days"` at execution time and an absolute
   range never moves. `tiles.window_warnings` says so at authoring time and
   `LoadResult.resolved_ranges` is what lets a tile label the window it actually read.

| module | role |
|---|---|
| `store.py` | `dashboards`/`tiles`/`layouts`/`runs`/`tile_results` in `analyst.db` |
| `tiles.py` | what a valid tile is: `validate` at authoring, `outgrown` on every refresh |
| `layout.py` | placement only; a partial layout is *completed*, never treated as hiding |
| `refresh.py` | `validate` (tiers 1-3) and `refresh` (+ tier 4), the atomic pass |
| `delta.py` | change since the previous refresh, single-row results only |
| `agent/tools/dashboards.py` | the eight agent tools — which do **not** include refreshing |
| `web/dashboards.py` | the HTTP surface; refresh lives here and nowhere else |

**The agent authors and arranges; the page runs.** Refresh is a button, deliberately: an
agent that could trigger one would turn "tidy up this dashboard" into two dozen warehouse
queries. The consequence to state rather than let someone discover — a tile the agent just
added has **no data** until someone refreshes, and the system prompt tells it to say so.

Two failure states, kept apart because only one is fixable by editing a tile: `refused` is
tiles that did not compile and nothing ran; `failed` is tiles that compiled and then broke
against the warehouse. A dashboard renders its last `ok` run, so neither blanks the tiles.

`tiles.CHART_TYPES` is exactly what `ResultChart` draws, and the two move together:
**renderer branch first, tuple second.** A validator that accepts a chart the renderer
cannot draw makes the tile misrepresent itself, so widening the tuple ahead of the branch
is the one order that is wrong.

**Nothing on the Python side validates a granularity.** `columns_of` builds a time column's id as
`{member}.{grain}` straight from whatever Cube echoed back, and `format.ts`'s `GRAINS` is the only
list of the valid set — it reads that id's tail to pick a date format, and a grain it does not
recognise renders as a raw `2026-01-01T00:00:00.000` in the cell. Python once carried a matching
`shape.GRAINS`; it had no callers and is gone, so the browser's copy is now the single source. Any
new grain goes there. (`shape.DATE_GRAINS` is a different question — which grains a CSV export
truncates to a date — and is used.)

`core.db.SCHEMA` will **never gain a column** — `CREATE TABLE IF NOT EXISTS` does not diff, so
adding a table to a live `analyst.db` works and adding a column silently does not. There is
no migration mechanism and deliberately so: this runs on one machine for one person, and
recreating the database is a cheaper answer than a chain nobody else will ever run.
`tiles.time_member` is written on every tile and read by nothing — it is there because the
feature needing it would otherwise require exactly that column. Retention is
the mirror image: a DELETE rather than a column, so the depth stays changeable — it is the
*index* that could not be added later.

Not built: promoting a tile to a `certified_queries/` file.

**Code execution** (`sandbox/` the client, `services/sandbox/` the service, `agent/tools/code.py`)
— a Python sandbox on the *query* agent, not an agent of its own. `sandbox/__init__.py` holds
the three invariants; read it before changing anything here.

| module | role |
|---|---|
| `services/sandbox/` | the service: a workspace per chat, scripts over HTTP, one token (19 tests) |
| `sandbox_service/runner.py` | one script as a subprocess, streamed, killable, timed out |
| `sandbox_service/workspace.py` | one chat's directory, and the only path guard there is |
| `sandbox_service/profiler.py` | what an uploaded file *is*, read once on arrival by duckdb |
| `sandbox/client.py` | the portal's client — `run`, `kill`, upload, download, profile |
| `sandbox/profile.py` | the `Profile` shape and what it says in a prompt |
| `agent/tools/code.py` | two tools: `export_result` and `run_python` |
| `web/files.py` | the HTTP surface: a file in, a file out, and taking one back |

**What keeps the gate load-bearing is that the sandbox holds no credential**, not a second agent
and no longer `--network none`. `run_query` and a Python interpreter are in the same hands, and
that is safe because the sandbox cannot sign a Cube token and has no warehouse password — there
is no route through it to a number that did not come through the four tiers. `export_result` is
the only way rows cross over. What this buys is the arithmetic a `CubeQuery` cannot express — a
ratio, a period delta, a share of a total — which the agent was otherwise doing in prose.

**`--network none` is gone, deliberately, and it is the one real loss.** A service that answers
HTTP has a port, so a script can reach the network and could send data it was already handed
somewhere else. Docker's `internal: true` networks would prevent exactly that but cannot have
published ports, and blocking egress inside the container needs `NET_ADMIN`. Accepted as the
price of the portal running nothing — and the reason `SANDBOX_TOKEN` is required rather than
optional, and the port is bound to loopback.

**Two tools and not a filesystem.** The arithmetic is a dozen lines, so a script arrives whole and
its output comes back. The service saves it anyway, which is what leaves a computed figure's
provenance on disk and lets a second attempt not overwrite the first. Filtering, grouping and
totalling stay in `run_query`, where the gate checks them and pre-aggregations serve them.

**The workspace is the state, and it is on the far side.** A script's files, an upload and its
profile all live in the service's volume; there is no table for any of them, because a row could
only ever be a second copy of `ls`. Deleting a chat asks the service to drop it — best-effort, so
a sandbox that is down cannot leave a chat undeletable.

**A script is shown before it runs, and that is not a safety gate.** `approve_scripts` exists
because the moment before a run is the only one where the script is readable. `run_python` emits
an `approval` event carrying the script — **the one event that carries a body**, and it is not
stored, because the file is at `scripts/run-NNN.py` and the outcome is on `tool_end`. The answer
comes back on a queue (`POST /chats/{id}/approve`), the same shape as the cancel flag rather than
a LangGraph interrupt: a confirm should not close the stream and resume a thread. Two consequences
worth keeping: **"always" is what makes it usable** — without it a write-run-fix loop is fifteen
clicks and the setting gets turned off — and **stopping the turn answers the question with no**.

**Stopping asks the service; it does not cancel the request.** Cancelling the stream would leave
the script running with nobody reading it. So `run_python` races the run against `session.cancel`
and calls `POST /kill`, which signals the process *group* — a script that spawned children leaves
them holding the workspace otherwise. The service holds the process object, so there is no pid
file and no `kill` inside an image with no `ps`; both of those traps went with the container.

**Output is streamed as `output` events — the one event type that is never stored**, because
`tool_end` already carries the whole of it trimmed and storing deltas as well would put an
untrimmed second copy in the event log. `agent/turn.py` holds that exemption. The service sends
JSON lines and the client re-frames them, so the `on_output` seam is the one `core.proc.run`
offered when this was a subprocess — nothing above it changed shape.

**Three caps, and the one that was missing.** The service bounds a call (`EXEC_TIMEOUT_S`) and
what it says (`OUTPUT_LIMIT`), but forty model calls of each is eighty minutes and 160,000
characters — so `TURN_SECONDS`/`TURN_OUTPUT` bound the turn, and `ToolCallLimitMiddleware` bounds
the count with `exit_behavior="continue"` so the agent answers from what it has rather than
stopping dead. The turn's numbers are provisional: no long write-run-fix loop has been measured.

**The profile is what makes an upload usable rather than merely present.** Without it the first
move after every upload is a `run_python` to discover the columns: it works, costs a round trip,
and comes out differently each time — and a 200 MB CSV cannot go into a context at all. The
service profiles on arrival and keeps the answer beside the file, so an upload is one round trip
and `GET /profiles/{chat}` is one small request per turn rather than one profiling run per file.
It is read by duckdb *in the sandbox* (`SUMMARIZE` plus one exact `count(DISTINCT)` pass, so
"this column is unique" is a fact rather than an approximation), never raises, and a file it
cannot read is a note the agent can act on rather than a failed upload.

**The web portal** (`web/`, `src/web/`, and the transcript beneath it) — `analyst` opens a
browser the way Jupyter does. One flat event stream is the contract between agent and browser:

| module | role |
|---|---|
| `core/events.py` | the `Event` schema and `emit()` — the wire format, in `core` because every toolset writes it |
| `transcript.py` | the `chats`, `events` and `results` tables, and the LangGraph checkpointer beside them. Storage only: turning a transcript back into provenance is `agent.session.replay` |
| `web/stream.py` | `sse()` — the wire framing and the headers, for both streams |
| `agent/turn.py` | `run_turn()` — one turn as an async iterator of events, for either agent |
| `web/server.py` | routes, SSE, token auth, static mount |
| `src/web/` | React source; **builds into `analyst_agent/web/static/`** |
| `src/web/src/api.ts` | `streamTurn()` — the one hand-rolled SSE reader, for both chats |
| `src/web/src/components/Transcript.tsx` | one event stream rendered, for both chats |
| `src/web/src/components/Rail.tsx` | the mode switch — chat, boards, data or agents, and nothing else |
| `src/web/src/components/McpPage.tsx` | agent access: the MCP switch, its token, and what to paste |
| `src/web/src/components/ChatHeader.tsx` | what is being queried, and a link to manage it |
| `src/web/src/components/ServedModel.tsx` | the compiled model **and** its grounding, one list |
| `src/web/src/components/ResultBlock.tsx` | one query's data: the table, the chart above it, CSV |
| `src/web/src/components/FileBlock.tsx` | what a script wrote: images inline, everything else a download |
| `src/web/src/components/Composer.tsx` | the question, and the files it arrives with |
| `src/web/src/components/ResultChart.tsx` | line / bar / kpi as inline SVG, from a spec |
| `src/web/src/components/Dashboard.tsx` | one board: the grid and **the only Refresh** |
| `src/web/src/components/Tile.tsx` | one tile: its chart or table, its window, its delta |
| `src/web/src/format.ts` | how a result value renders — cells, axis ticks, sort order |

**The HTTP surface is four nouns, in the order the work happens.** Anything new goes under the
one it belongs to; a fifth prefix is a claim that there is a fifth thing.

| prefix | module | what it is |
|---|---|---|
| `/api/connection` | `web/connection.py` | the credentials, and whether they are answering. `GET` returns the whole body every write route ends on — row, health, serving, stages |
| `/api/deployment` | `web/connection.py` | the addresses, the secrets, and the compose file per service. Two nouns in one module because connecting is two steps and the second is the deployment |
| `/api/pipeline` | `web/pipeline.py` | the four stages, the job running one, the tables to pick from, and `DELETE` to discard what was built |
| `/api/model` | `web/model.py` | what Cube serves, its grounding, and the refs/files/merge the editor writes through |

Plus `/api/chats`, `/api/dashboards`, `/api/results` and `/api/mcp`, which are the products
rather than the plumbing. **"Catalog" appears nowhere in it**, deliberately: the name means
`cube.catalog.Catalog` in Python, and the route that used to be `/source/catalog` returned the
warehouse's tables — the opposite thing. It is `/api/pipeline/tables`, and the served model is
`/api/model` with `/api/model/summary` for the cut-down copy the chat page reads.

Two invariants: **the event log is the transcript** — replaying a chat replays its
events, and `Session` provenance is rebuilt from it (`agent.session.replay`), so nothing lives
only in memory. And **tools author their own events** through LangGraph's stream writer
rather than the service inferring them from chunks, so a gate rejection reaches the
browser with its reasons attached.

`output` is the single exception to the first, and deliberately narrow: a running script's stdout,
live only, because the whole of it lands on `tool_end` trimmed and a replayed transcript reads it
from there. Any *other* wire-only type would be state that exists nowhere — check that the whole
thing is already stored somewhere before adding one.

**Both agents share every seam.** `agent/tools/trace.py` writes every `tool_start`/`tool_end`
in the codebase; `agent/turn.py`'s `run_turn` is the one loop, taking a callable that builds
either agent; `api.streamTurn` is the one SSE reader; `events.applyEvent` is the one reducer and
`Transcript.tsx` the one renderer. Each of those was two copies at some point, and the copy
always lost something — the editor chat's own four-type reducer dropped `thinking` and
`compacted` on the floor for as long as it existed, so the editor agent's reasoning was emitted
and never shown.

`parent_id`/`depth` are on every event and always `None`/`0`. They are reserved for the
orchestrator's subagents so that adding nesting never changes the protocol.

**Rows are not in the trace, and the trace is not where data is read.** A `run_query` step
keeps the query, the SQL and the gate's reasons behind its caret; the rows it returned are
their own transcript block below the answer. Three rules hold that together:

*The event carries a head, the `results` table carries the whole thing.* `tool_end` holds
`rows[:MAX_POINTS]` so a replayed transcript draws every table and chart with **no
requests at all**, plus a `result_id` for the rest. `visualize.MAX_POINTS` is one constant
for both the event's row count and the largest chart offered — a chart offered for more
rows than the event holds would plot the first hundred of four hundred months as if that
were the series. `preview_rows` (30, the model's copy) is a separate number because the
model pays per token and the browser pays per event row.

*The header and the chart are derived, never asked for.* `cube/shape.py` and
`cube/visualize.py` are pure functions of (query, catalog, rows), the same argument
`shape.describe_query` makes for its sentence. `chart_for` returning `None` is a correct and common
answer. `set_chart` lets the agent override, validated against that result's real columns
by `validate_spec`, and it authors its own `tool_start`/`tool_end` like any other tool —
which is why charts needed **no new `EventType`**.

*The transcript is in the order things happened.* `events.ts` appends and never reorders, so a
result sits under the query that fetched it and above the prose about it. `applyEvent` used to
lift every `result` and `files` item to sit *after* the answer, on the argument that the claim
should precede its evidence — which reads well for one query and badly for two, where both tables
end up below the answer and the reader matches them to steps by name. If that is ever revisited,
note that the fix is not a rule about the tail: the agent reasons *after* reading a result, so
items arrive as `[… tool, result, thinking, text]`.

`files` needed **no new `EventType`** either. `run_python` names its files on its own `tool_end`
as `wrote`, which is the `set_chart` argument again — paths only, because the file is in the
sandbox service's workspace and the download route is how it is read.

**The browser is separate products, so it is separate modes.** `Rail.tsx` owns the switch
and the column beside it swaps whole — `ChatNav`, `DashboardNav` or `DataNav`, never two.
Chat and data shared one sidebar until the chat list stayed live and clickable while you
were administering a database. Boards sit between them because that is the order the work
happens in: made in chat, read against the model. Agents is last and apart from data: it is
not "what am I connected to" but "what does something else get to call". The mode is read off
the hash and nowhere else (`useHash`), as is which dashboard is open; a mirrored React copy
disagreed with the address bar after a back button. A hash and not a router — four views do
not justify one. `Rail.Mode` is the union, defined beside the switch that owns it.

**Data and agents have no middle column** (`.app.no-nav`), because there is one connection and
one endpoint, and a sidebar listing either would be a list of one. Chat and boards keep theirs.

`ChatHeader` is where "what am I querying" lives: the chip carries the database's name, the
health dot and `servingNote` — the size of the model, which is what says it is *built* and not
merely connected. It once carried the checked-out branch too; a question chat is pinned to main,
so there was never more than one answer for it to show. The chip is a **link to the data page
and nothing else**. `ServedModel` does not belong in it: "what can I ask about" feels like a
question you have with a cursor in the composer, but it is the wrong depth for a dropdown —
clicking the chip to check which database you are on would open an admin screen over the
transcript. It lives on the data page only, one deliberate click away.

`ServedModel` is **master–detail, not accordions**: the entity list is a fixed column and
members render in the box beside it, because expanding in place pushed every row below
down the page. The list stays in name order even while filtering — only the *default
selection* follows the best match, since re-ranking rows under someone who is typing moves
what they are reaching for.

**Its sections are collapsed by default** and there is no selection on arrival. Five
sections open at once is a column of eighty rows; shut, the shape of the whole model fits
on one screen, which is why the count stays on the header. Two consequences that are not
preferences: a filter force-opens every section, because typing into a column of shut
headers and watching only the counts change is a search that appears to do nothing; and
the best-match selection now applies *only* under a filter, since a detail box
volunteering the first view would make Views the one section that is never collapsed.
Nothing else force-opens — collapsing a section while its row is in the detail box is
allowed, and leaves the detail alone.

**Grounding is sections of that same list, under Views and Cubes** (`Grounding.tsx`
supplies the sections and the artefact detail; `Section.tsx` is the header chrome both
halves share). It is the continuation of the same question — Views and Cubes say what a
question can reach, grounding says what the agent already believes before the question —
and as its own panel one of the two went unread. The two halves **fail independently**,
which is the thing to keep straight when editing it: the model needs the container up and
Cube compiling, grounding is markdown on this machine. So a Cube failure is a banner over
empty Views and Cubes, never an early return over the whole list, and `unavailable` (a
stopped container) is a separate prop from `model.serving === false` (Cube answered
badly). Anything that would early-return over the whole list is the sections' empty copy
instead — a Cube problem must not hide grounding, which has nothing to do with it.

The default selection stays on the model when there is one — an artefact only opens when
nothing else survives the filter, or when there is no model at all.

`SourceDetail` shows **four** stages where `evidence.STEPS` has three: `read` writes no step
row, but hiding it inside "Build cubes" meant expanding the first of three steps offered
a button that did something else. `evidence.STAGES` is the one definition of the sequence and
`evidence.stages` is the one place that knows `read`'s state comes from the cached catalog
rather than from `steps`; the browser iterates the rail it ships on `SourceRow.stages`.

## Storage

**One file, one schema.** `core.db.SCHEMA` is every table this process creates, and
`~/.analyst/analyst.db` holds all of them: `chats` and `events` (the transcript),
`results`, `dashboards`/`tiles`/`layouts`/`runs`/`tile_results`, `deployment`, and
LangGraph's `checkpoints`/`writes` — those last two created by `AsyncSqliteSaver` rather
than here. `core.db.connect` is how it is opened, so `PRAGMA foreign_keys` cannot drift between
the three stores that open it.

The schema lives in `core/db.py` rather than beside each store because **the unit is the file,
not the store**: `ChatStore`, `DashboardStore` and `DeploymentStore` all write it and none owns
it, and a schema split across three modules reads as three databases.

The file is **chmod 600** — `DeploymentStore.init` sets it, because `deployment.credentials` is
plaintext JSON and holds the warehouse password, and the file mode is the whole guard. The one
other place those values leave the process is `GET /api/deployment/{service}/compose`,
deliberately.

One person on one machine: the token guard is the whole access story, and
`cube/client.py`'s `LOCAL_CLAIMS` is the single literal signed into the Cube JWT.

**A file is not a row.** An uploaded CSV, a chart a script drew and a file's profile all live in
the **sandbox service's** volume, one directory per chat, and none of them has a table. They are
on the far side of an HTTP boundary, so a row describing them could only ever be a second copy of
`ls` — and the copy is what would go stale. Deleting a chat asks the service to drop its
workspace, best-effort, so a sandbox that is down cannot leave a chat undeletable.

## The deployment

**The portal runs nothing at all.** Not Cube, not the model store, not the sandbox, not a probe.
It holds the credentials, mints the secrets, renders a compose file per service, and is told
where things are. **There are two deployments**, each with its own compose file and address,
sharing nothing:

- `services/semantic/` — `model-store`, `cube`, `cubestore`. Reached at the store: **Cube's port is not
  published**, and the store proxies it at `/cube/{path}`, minting Cube's JWT from the ref in
  `X-Model-Ref`. One authenticated door, and `CUBEJS_API_SECRET` never leaves that compose file.
  **`cube` is our image, not `cubejs/cube`** — `cube/Dockerfile` bakes `cube.js` in, because it is
  portal-authored logic rather than user configuration, and a bind-mounted copy meant the
  deployment could only run from a directory that happened to have the file.
- `services/sandbox/` — where the agent runs Python. **Optional to the code and required by
  onboarding**: nothing but `run_python` needs it, and the wizard will not finish without one.

**One deployment, so one row in `deployment`** — credentials and secrets included, with
`id = 1` enforced by a CHECK rather than by convention. `datamodel/deployenv` is the one place
that decides what a deployment needs, and it **generates** the hand-over — with no `.env` and
nothing written to disk.

**The templates are inlined in `deployenv` as `SEMANTIC` and `SANDBOX`**, not read from
`services/`. They were read off disk so that there was one file rather than a tested one and a
shipped one; the price was that `uv_build` ships only `src/analyst_agent`, so **a wheel could
not render a hand-over at all**. The copies under `services/` are now what the images are built
from and what `store_check.sh` runs; the inlined ones are the same files with the `build:`
context dropped and every tag static. The cost is two copies that can drift, accepted
deliberately. `render` refuses to emit anything with a `$` left in it, because a file with an
unresolved variable reads as complete and dies at `up`, and every `${VAR}` is required — empty
counts as missing, the way it does to compose.

One substitution is a **marker rather than a variable** (`ANALYST:CREDENTIALS`), because it is
not a scalar: *which* `CUBEJS_DB_*` keys a warehouse needs is per-connection — postgres has
host/port/name/user/pass where Databricks has a URL and a token — so no fixed set of lines in
the file could serve both. A hardcoded postgres-shaped block is exactly how this broke the first
time. The marker's whole line is replaced at its own indentation, which is what keeps the YAML
valid.

`GET /api/deployment/{service}/compose` is the only route in the codebase that returns credential
*values* — handing them over is its entire purpose, since nothing can start the deployment
without them. It answers with `Content-Disposition: attachment` and the page never renders the
body: the file holds the warehouse password and all four secrets, and the only thing to do with
one is run it somewhere.

**Every image tag is static in the template, and the portal chooses none of them.** It is
pinned to `deployment.CONTRACT` — a compatibility integer and not a version, because the only
thing the portal cares about is whether the service speaks the API it was built against, and
`latest` is how that stops being true silently. But the pin lives in the template's text, so
bumping `CONTRACT` means editing both templates. That is the same argument as the ports below:
what the deployment runs on is the file's business, and a warehouse needing a different Cube
build (`databricks-jdbc` wants `analyst-cube:1-jdk` and `platform: linux/amd64`) is an edit to
the file that was handed over. The templates' headers say so.

**The names are unqualified, so the images are local**: nothing is published, and a registry
prefix would render a file that fails at `up` with a pull error instead of using what was built
from this tree. `analyst-model-store:1`, `analyst-cube:1` and `analyst-sandbox:1`, built from
`services/*/` — `store_check.sh` builds the first two itself if they are missing, which is also
why the live check is what proves the *baked* `cube.js` rather than a bind-mounted one.

**The portal has no opinion about ports.** It is told an address, so it never reaches a guessed
one — which means the published port belongs to the compose file that publishes it, left to its
own `:-` default. A constant on this side would be a second place deciding it, and the one that
cannot be edited: someone whose 8500 is taken changes the file they were handed, and nothing in
the portal needs to know they did. So `test_only_the_store_publishes_a_port` asserts *one*
published port that is not Cube's, rather than a number.

That row is a table in `analyst.db` like everything else — chats, dashboards, the deployment and
the checkpointer in one file under `DATA_DIR`. So `DATA_DIR` scopes the whole install rather
than just the history: a scratch directory is a scratch *everything*, connection included.

**Two addresses on the row, and neither of them is Cube's.** `store_url` is the semantic layer
— model *and* queries — and `sandbox_url` is the sandbox. Where Cube actually runs is the store's
`CUBE_URL`, its business rather than this row's. `CubeClient`, `ModelStore` and `SandboxClient`
each read one address, and `open_client`/`open_store`/`open_sandbox` re-read the row per use so
an edited address leaves no stale copy anywhere.

**The store's write token is what queries.** The read token is Cube's own and the proxy refuses
it — letting it query back through would make the credential handed out as capable as the one
kept. `api_secret` is still on the row, but only as a value the portal *mints and hands over* in
the compose file it renders: nothing here signs with it, and the store is what does.

**Connecting is two steps, because the portal cannot do the middle one.** `POST /connection`
stores the credentials and mints the secrets with **no addresses**; someone runs the compose
file; `PUT /deployment` receives `store_url` and `sandbox_url`. `deployment.deployed` is
`store_url` being set — the sandbox is not part of it — and it is deliberately *not* a health
state: "nothing has been run yet" is the normal condition between the steps, so it is neutral
rather than red. `onboarded` is the one that wants both, because the wizard's sandbox step is
required. The same PUT is how a deployment that moved is followed, so there is one path rather
than two — and it **checks each address before storing it**, so one that does not answer never
has to be undone. `PATCH /connection` is credentials only.

**Which `CUBEJS_DB_*` variables a warehouse needs is not this repo's business.** The form takes
arbitrary name/value pairs and `POST /connection` validates one thing: every name starts with
`CUBEJS_DB_`. That prefix is the whole guard, and it is what makes arbitrary keys safe —
everything the template sets itself is `CUBEJS_` without the `DB`, so a typed key can never
overwrite the API secret or dev mode. There is no per-driver field table on this side at all:
the dropdown and its starting rows live in `ConnectionForm`, as prefill rather than schema. A
warehouse nothing here has heard of is configured the same as Postgres, and file-based
credentials (a BigQuery key) go in as a variable Cube reads rather than as a mount.

**Four conditions, and conflating them is the mistake to watch for.** `deployed` (an address is
stored) → `onboarded` (both are) → *reachable* (something answers and it is the right thing) →
*serving* (Cube is compiling a model). `CONTEXT.md` defines them; the point here is that only the
first two are properties of the row and the other two cost a request.

**Reachability is two questions, not one probe** — `reachable.py` holds both and
`docs/adr/0001` says why merging them is wrong. `semantic`/`sandbox` check a *pasted* address
end to end (identity, contract, an accepted token) for the wizard, through `test`, which is the
only one that takes the URL as an argument so a failing address never has to be undone.
`querying`/`alive` check the *stored* one as cheaply as possible for the health dot and the
Python banner. The asymmetry is real: `/cube/readyz` is served through the store's proxy, so one
request already proves the store is up, took the token, and has Cube behind it — the full chain
adds only identity and the contract number, which matter once and are waste every fifteen
seconds. `PUT /deployment` is the one write that runs a check, and it is `test` it runs.

**What Cube is serving is a different question again**, and lives in `datamodel/serving.py`.
`state()` names why `Serving` is null — no deployment, not answering, or a model Cube refuses to
compile — and it takes `deployed` rather than `running`, because **the portal starts nothing and
so can never observe a stop.** `STATES` is what the browser's copy is keyed on.

**Connecting is also a replacement, not an addition.** `DeploymentStore.save` is
`INSERT OR REPLACE` and resets `steps` and `state` with it: those describe the tables of a
database that is no longer connected, and keeping them would offer a "Build cubes" that
rewrites the model from a catalog nothing read.

**Editing the credentials invalidates nothing, and that is a decision.** There was an
`IDENTIFYING` tuple of six key names that `identity()` hashed to decide whether the connection
now pointed at a *different* database, and `PATCH` called `reset_progress` when it changed. It
is gone: a changed host may be the same database moved and a changed name may expose the same
tables, so which happened is not derivable from the values — and it only ever cleared `steps`
and `state`, never the served model, so it was not preventing a stale model from answering
either. `DELETE /pipeline` is the explicit replacement, behind a confirm on the data page. The
consequence to know: after repointing at a different database, running `cubes` without running
`read` first builds from the **stale cached tables**. Cube compiles that happily and the queries
fail at runtime.

**The catalog probe is a ref, not a container.** `read_catalog` commits its introspection cubes
to a `probe` ref, asks the connected Cube about that ref, and deletes it — deleting it again on
the way in, since a crashed read would otherwise leave one in the ref list that nobody created.
Tables and columns come from `information_schema.columns` (`read.COLUMNS_SQL`, every driver
rather than per-dialect) because `/playground/db-schema` **is served only in dev mode**, and a
deployment on a server is not in dev mode. Keys and statistics stay per-dialect in `DIALECTS`.
Losing columns or keys is fatal; losing statistics is a note.

**Nothing here knows what docker is, and the sandbox being down is never fatal.** A machine with
no docker runs the portal perfectly well — every service is wherever its owner put it, so this
machine's own state is not a precondition for anything. `state.probe` asks `reachable.alive`
and ships the reason on the connection body as a capability rather than a precondition — cached
with the health dot under the same TTL, so it costs no request of its own: without a sandbox
there are no
ratios, period deltas or charts drawn from a script, and everything else works.

**A chat has a `kind` and a `ref`.** `'question'` runs the query agent, `'model'` the editor
agent; both share this table and the transcript format and differ only there. `list_chats`
filters on `kind`, so the sidebar shows question chats and the model editor shows its own.
`ref` is the model ref a `'model'` chat edits, fixed at creation and read by `stream_edit_turn`
rather than followed from the page — a transcript that tracked a dropdown would describe edits
to a model it was not editing.

`core/jobs.py` runs **one job at a time, machine-wide**, guarded by a `flock`: two portal
processes rebuilding one model store would race on the same ref. A job is not stored — it lives
in memory, streams to whoever is watching, and writes its outcome onto the deployment row's
`steps` as it goes. Refreshing the browser re-attaches (the buffer replays); restarting the
server loses the narration, not the result.

## Things that will cost you an hour

- **`python-multipart` is a separate dependency and `UploadFile` fails at *request* time
  without it.** Not at import, so the upload route looks like a working route until the first
  file — `import multipart` raising `ModuleNotFoundError` is the whole diagnosis.
- **`read_csv_auto` will read a PDF and report a shape for it.** duckdb sniffs anything as one
  column of nonsense rather than refusing, so `profiler.view` matches named extensions and raises
  for the rest. A confident wrong profile is worse than "not a tabular file".
- **A literal `../` never reaches a route — every client collapses it before it is sent.** So
  `GET /files/../../analyst.db` arrives as `/api/analyst.db` and answers 405, and a test written
  that way proves nothing. The guard cannot be the URL's shape; it is the sandbox service's
  `workspace.resolve` on the *resolved* target, and the test has to percent-encode
  (`%2e%2e%2f`) to reach it at all.
- **A file dropped anywhere but the drop target is *navigated to*, replacing the page.** So the
  composer's listeners are on `window` rather than on itself — it only exists in the chat view, so
  that is the right scope — and a drop lands in the composer wherever it was released.
- **A run's new files cannot be a set difference of names.** A script that re-draws `chart.png`
  adds no name, and the run whose picture is on screen would report having written nothing.
  The service's `workspace.snapshot` keys on `(size, mtime)` for exactly that.
- **`get_stream_writer()` fails two different ways, and a unit test hid the second.** With
  no config at all it raises the documented `RuntimeError`. Calling a tool's own
  `.ainvoke()` outside a graph produces a config that exists but holds no runtime, and the
  lookup dies with `KeyError('__pregel_runtime')` — which sailed through the bare
  `except RuntimeError` in `core/events.py` and killed the call. Fourteen passing tests
  missed it because the ones that assert on events **monkeypatch `emit`**, so the real
  no-op path had no coverage until a script drove `add_tile` directly. When a fixture
  replaces the thing under test, add one case without the fixture.
- **`StreamReader.readline` raises past 64 KiB**, so a streaming reader has to take chunks: one
  `print` of a wide dataframe has no newline in it and would fail rather than stream. True on both
  sides of the wire — the service reads the subprocess in chunks and the client reads the response
  in lines, which is why the service frames its own output as JSON lines.
- **`timeout` and `ps` are coreutils and procps, and neither is a given.** The sandbox service
  enforces its own limit with `asyncio.wait_for` and kills with `os.killpg`, rather than wrapping
  the script in `timeout` — which does not exist on macOS at all, so the service's suite could not
  run outside a container. This is only possible because the service holds the process object;
  when this was a `docker exec` there was nothing to reach around, which is what the pid-file trick
  and `kill -TERM` through `bash -c` existed for. Both are gone.
- **Signal the process *group*, not the process.** A script that spawned children leaves them
  running and holding the workspace if only the interpreter is signalled. `start_new_session=True`
  is what makes `killpg` available to do it.
- **Every cross-seam call has a cap on both sides, and the smaller one is what a person sees.** So
  the question is who reports. Cube-bound: the portal, because the store answers a bare 502 when
  its own leg gives up, so a cap above the store's can never elapse and a slow query reads as a
  proxy failure. A sandbox run or upload: the service, because it spawns and saves, so a client
  giving up first abandons a success. That was live for uploads — the client's 30s default against
  a 60s profiler 502'd the browser for a file the service had saved *and* answered for, and the
  service is built so profiling can never fail an upload. `tests/test_timeouts.py` asserts both
  directions by reading the services' constants out of their source, since they are separate
  projects; it proves the tree, not a running deployment.
- **`httpx` transport errors do not subclass `OSError`.** An `except OSError` around a Cube
  call catches nothing useful — a plain `ConnectError` went straight through and 500'd the
  whole dashboard page for a stopped container, which is the exact failure that branch
  existed to prevent. The established pair here is `(CubeError, httpx.RequestError)`; grep
  for an existing except-clause before writing a new one.
- **Cube accepts `gt`/`gte`/`lt`/`lte` on a time dimension even though its docs list them under
  number dimensions only.** Verified against a live source: it answers correctly. `catalog.TIME_OPS`
  therefore includes them, and trimming the list back to what the docs say rejects a query that
  works. `afterDate`/`beforeDate` and friends stay the idiomatic form the prompt steers toward.
- **A view's member grouping is `members` in `/v1/meta` and `includes` in the model YAML.** Same
  concept, two key names, which is the whole reason `catalog.Folder` and `decide.FolderDecision`
  are separate shapes rather than one reused across the read and write sides.
- **Cube's `order` has two shapes and the agent invents a third.** Cube takes
  `{"member": "asc"}` *and* `[["member", "asc"]]`, so `CubeQuery` normalises both to the
  object form. The model reaches for `[{"id": ..., "desc": true}]` — the Playground's
  internal format, which the query API does not accept — and pydantic's own rejection for
  it reads "Input should be a valid dictionary", which says what arrived and never what to
  send. A tier-1 message the agent cannot act on costs a whole retry, so
  `accept_both_order_forms` names both shapes and names the Playground one as the mistake.
- **`ChartSpec` is a pydantic model and `Column` is a dataclass**, and `run_query` serialises
  one of each into the same event. `asdict` on the model raises `TypeError` while the event
  is being *built*, which kills the turn — and every event assertion still passes, because
  those tests monkeypatch `emit`. `tests/test_query_tools.py` drives the tools with `emit`
  left alone for exactly this.
- **A stored `dateRange` is not a window — Cube resolves it at execution time.** So
  `"last 30 days"` moves on every refresh and `["2026-01-01", "2026-01-31"]` never does,
  and nothing in the stored query distinguishes them. `LoadResult.resolved_ranges` reads
  the range out of the query Cube *echoes back* (defensively — a body that does not look
  right yields nothing, because a tile that cannot label its window is cosmetic and an
  exception there would fail the query).
- **A chart is chosen on the data, so it cannot be validated once.** `visualize.chart_for`
  takes `rows` because bar count and series count are properties of the data, not the
  query — guessing them from `limit` is how a query with no limit got three hundred bars.
  Hence two entry points: `tiles.validate` when a tile is written, `tiles.outgrown` on
  every refresh. A tile authored as twelve bars in January is four hundred by December and
  nobody re-authors it.
- **A view's members are the cube's members again, so flattening every entity in
  `/v1/meta` counts the model twice.** The Serving panel reported "48 measures, 104
  dimensions" for a model with 72 members. Counts are over cubes only; a view's size is
  how much of that surface it exposes, not an addition to it.
- **`aliasMember` is the only thing in `/v1/meta` that says what a view is made of.**
  `sales.revenue` carries `aliasMember: invoice_line.line_amount`, and a cube's own
  members carry none — which is what makes it usable as "projection or real thing". It is
  parsed onto `MemberDoc.alias_member`; the view→cube and cube→view lists in
  `web/model.py` are both derived from it. Without it a view and a cube are two entities
  differing by a type string, which is how they were being presented.
- **Views omit surrogate keys on purpose,** so "no view exposes this member" is true of
  every `*_id` in a healthy model — 11 of 72 on chinook. Styling that as a warning reports
  a correct model as a dozen problems; the actionable case is a *cube* no view reaches at
  all, which is flagged on the entity instead.
- **Cube compiles every `*.yml` anywhere under the model mount, not just `cubes/` and
  `views/`.** Verified live: a `grounding/certified_queries/probe.yml` took chinook from 13
  cubes to **0**, with `Compile errors: ... Unexpected YAML key: name. Only 'cubes',
  'views', and 'view_groups' are allowed here` — the error names the grounding file, and
  every query is down until it goes. A `.md` in
  the same place is ignored entirely, which is why grounding is markdown and why
  `_load_dir` globs `*.md`. There is no top-level Cube key for a saved query, so no
  "Cube-compliant" YAML can hold one; the only free-form slot is `meta:`, which
  `publish.write` regenerates. If grounding ever needs a structured format, that is a
  decision to make grounding *generated* — not a way to keep hand-written files.
- **A model change is live without a restart, and nothing here restarts Cube any more.**
  `cube.js` gives Cube a `schemaVersion` that is called on *every request* and returns the ref's
  sha, so a commit is picked up within the 5s version cache. Measured both ways: a new
  `views/x.yml` served in under a second, and a bad `join_path` taking the whole ref to a compile
  error just as fast, then recovering the moment it went. The consequence is that the edit gate
  refusing bad YAML *before* the commit is what keeps Cube up — there is no restart afterwards to
  hide behind. A whole model changing at once is safe for a different reason: it lands on a
  candidate ref, which has its own compiler cache entry (`contextToAppId`), so it is compiled
  from a known state rather than from a half-written one.
- **Cube qualifies a single-identifier `sql:` but passes an expression through verbatim.**
  `sql: unit_price` becomes `"invoice_line".unit_price`; `sql: quantity * unit_price`
  reaches the database unqualified and dies with `column reference "unit_price" is
  ambiguous` as soon as a join brings in another table with that column. Bare columns are
  written bare (matching Cube's own generator); multi-column expressions are qualified
  with `{CUBE}` by `decide.qualify_expression`.
- **`CUBEJS_DEV_MODE=true` disables JWT verification** and is what serves
  `/playground/db-schema` — which is why nothing here reads that endpoint any more. Fine
  locally; never for anything real.
- **A database on this machine is `host.docker.internal` from inside a container**, not
  `localhost`.
- **Escape model/Cube/database text before `console.print`.** Rich reads `[...]` as
  markup and silently swallowed the gate's `[rule-id]` prefixes. Use `rich.markup.escape`.
- **Nothing is written into the source tree, and `services/` is only read from it.**
  `services/*/` stays beside the package rather than inside it because each is a second Python
  project with its own lock file and tests. Nothing reads it at runtime any more — the compose
  templates are inlined in `deployenv`, which is what lets a wheel render a hand-over. Earlier
  this resolved from **this file's own path and never from the working
  directory** — where the process was started is not a fact about the install, and a portal
  launched from a home directory used to find nothing. `services/*/` stays beside the package
  rather than inside it because each is a second Python project with its own lock file and
  tests; the price is that `uv_build` ships only `src/analyst_agent`, so **a wheel has no
  `services/` and cannot render a hand-over at all**. `registry.services()` raises with that
  sentence rather than letting a `FileNotFoundError` surface from inside a render. Everything
  written goes under `DATA_DIR` (`~/.analyst`): `analyst.db` and the job lock. Do not put
  scratch into the project.
- **Re-running `cubes` still discards the views and descriptions — it just cannot lose them.**
  `publish.install` replaces the whole model in one commit on a ref, so what was there is the
  parent commit and `GET /diff` shows exactly what the rebuild changed. Do not add a
  snapshot/restore pair: one-deep, silently overwritten, and strictly worse than the history
  that is already there. The picker still warns before a run that discards later steps, because
  recoverable is not the same as intended. (This used to be the worst trap in the codebase:
  `publish.write` unlinked every `*.yml` in the *served* model before writing, so a model Cube
  then refused left nothing running and nothing to recover from.)
- **A container's environment cannot be changed, which is why editing credentials cannot
  apply them.** `PATCH /connection` updates the row and the next render picks it up; the running
  Cube keeps the old password until whoever owns it re-runs the compose file. The form says so
  rather than reporting success. The same applies to `CUBEJS_DEV_MODE` and `CUBEJS_API_SECRET`.
- **`${VAR:?}` has to mean unset *or empty*, and honouring only "unset" is not a near-miss.** An
  empty secret renders as `CUBEJS_API_SECRET:` with nothing after it, `render` then drops that as
  an empty mapping (the branch that exists for an unpinned `platform:`), and the handed-over file
  comes out with no API secret at all — so the deployment starts cleanly and refuses every query.
  `deployenv._value` treats empty as missing, the way compose itself does.
- **`cubejs/cubestore:latest` is amd64 only.** The pull fails outright with "no matching manifest
  for linux/arm64/v8" rather than emulating, so an Apple Silicon host needs
  the handed-over file edited to `cubejs/cubestore:arm64v8`. `deployenv`'s template says so in
  its header and **does not pick the tag** — the portal has no opinion about what a deployment
  runs on. `services/semantic/tests/store_check.sh` picks it from `uname -m`, because that one
  runs here.
- **Production-mode Cube refuses to run a query without Cube Store.** `/v1/meta` answers fine and
  every `/v1/load` dies with "Cube Store was specified as queue/cache driver", so the compose
  file ships three services. Only `CUBEJS_DEV_MODE=true` embeds one — and it also disables JWT
  verification entirely, which is why the generated file defaults to production mode.
- **Dev mode still populates `securityContext` from the token.** Verified live: with
  verification off, `ref=candidate` compiled separately from `ref=main`. So per-ref serving works
  in either mode, and dev mode is a JWT decision rather than a functional one.
- **A progress sink may return a value.** The web's sink returns the `Event` it emitted, so
  `steps.Report` is `Callable[[str, dict], object]` and `_say` calls it without reading the
  result. Awaiting that result kills the job on its first progress call.
- **`body { line-height: 1.6 }` is inherited by buttons but not by inputs**, so identical
  padding gives a 32px button beside a 27px input beside a 27px icon button. Every control
  that shares a row takes `min-height: var(--control)` and `line-height: 1.2`; do not size
  a new one with padding alone.
- **`.container button` also matches buttons inside components nested in that container.**
  `.detail-primary button` was restyling the overflow menu's items into bordered grey
  boxes, because it is later in the file than `.menu-panel button` at equal specificity.
  Use `>` when a container holds a child component that renders its own controls. `.composer
  button` is the same trap sprung again — it paints Send, and it reached the ✕ on every
  attachment chip, so both chip buttons are styled at `.composer .attach-*` to outrank it.
- **`overflow-y: auto` computes `overflow-x` to `auto` as well.** An absolutely positioned
  popover wider than such a container does not spill out of it — it makes that container
  scroll sideways, and the content is unreachable. `Info` is `position: fixed` and placed
  from `getBoundingClientRect`, which is what makes it safe inside a scrolling panel. It
  closes on scroll because a fixed panel does not travel with its anchor.
- **`.panel` is in the DOM with no rule behind it.** `ToolCall.tsx` renders
  `<div className="panel">` and `styles.css` defines only `--panel` (a colour token),
  `.panel-head` and `.panel-toggle`. Defining `.panel` retroactively restyles the tool
  trace. This is the `.source`/`.dot` trap already sprung — the name is taken by markup
  rather than by CSS, so grepping the stylesheet alone will not find it. (`.panel-head` is
  also defined twice at equal specificity, so the data-page copy wins on `gap` and
  leaks a `margin-bottom` into the transcript.)
- **`.answer.streaming > :last-child::after` is the streaming caret**, so anything appended
  as the last child of `.answer` grows a blinking cursor. A result block is a sibling of
  `.answer` inside `.row`, never a child. Relatedly, `.answer-tools` reserves its height
  while invisible, which opens a ~38px hole under an answer; there was a `:has()` rule pulling
  `.res` back up, removed with the reordering — results now precede answers, so the two are
  no longer adjacent. Anything that puts them back in that order needs it again.
- **Cube returns a grouped time dimension under two keys**, the bare member *and*
  `member.grain` (`sales.invoiced_at` and `sales.invoiced_at.month`). `Object.keys(rows[0])`
  therefore invents a duplicate column, which is why a result's header is computed by
  `shape.columns_of` and stored beside the rows rather than recovered from them.
- **`/v1/load`'s `annotation` object is a second source of member `format`** and is
  discarded in `client.py`. Deliberately: `MemberDoc.format` comes off the cached `/v1/meta`
  catalog, which is already threaded everywhere a query is built, while `annotation` would
  have to be plumbed through both `LoadResult` and `GateResult` to reach the same place.
- **A FastAPI path parameter matches a dotted filename**, so `/api/results/{id}.csv` is
  swallowed by `/api/results/{result_id}` as `result_id="abc.csv"`. The CSV route is
  `/api/results/{id}/csv` — the same lesson as `/pipeline/{stage}`, one layer down.
- **`ROWS` is a SQLite keyword**, so the `results` table stores its rows in a column named
  `data` — which is also what `events` calls its JSON blob.
- **Editing `core.db.SCHEMA` changes new databases only.** `CREATE TABLE IF NOT EXISTS` does not
  diff, so a new *table* appears on an existing `analyst.db` and a new *column* silently does
  not. There is no migration mechanism; changing a column means recreating the file, which is
  affordable precisely because this is one local install. Every INSERT still names its
  columns, so that a schema edit is a loud error rather than values shifted one place left.
- **New CSS goes on new class names — check `styles.css` first.** `.dot` is the rail's brand
  dot; reusing a taken name for the sources panel inherits `display: flex` and a monospace
  font, and every card lays out sideways. The trap is that a name may be claimed by markup
  rather than by CSS, so grepping the stylesheet alone will not find it.
- **A FastAPI path parameter as broad as `/sources/{name}/{action}` swallows its
  siblings**, so `/sources/{name}/read` never matched. Pipeline actions live under
  `/api/pipeline/{stage}`, which is safe because the reads moved out from under that prefix —
  `/pipeline/tables` is two segments and `/model`, `/connection` are elsewhere entirely. The
  stage name is still validated against `STAGES` rather than accepted, so a typo 404s instead of
  starting a job named after it.
- **`CUBE_API_SECRET` and `CUBEJS_API_SECRET` are one secret with two names**, and HS256
  needs at least 32 bytes. `mint_token` refuses to sign below that rather than let pyjwt
  print `InsecureKeyLengthWarning` on every request. It is minted once at connect and written
  into the deployment's compose file, so rotating it means re-rendering and a re-run of the
  compose file — a container keeps the old key until it is recreated. `CUBEJS_DEV_MODE=true`
  skips JWT verification, so a mismatch will not surface in dev mode; it fails the moment
  dev mode goes off, which is the default the generated file carries.
- **Reasoning summaries need Azure api-version `2025-03-01-preview` or later.** Below it
  the same deployment returns plain string content, zero reasoning tokens, and a 400 on
  `use_responses_api` — so a wrong `AZURE_OPENAI_API_VERSION` shows up as a failed model
  call rather than an empty thinking panel.
- **`reasoning: {"summary": "auto"}` frequently emits nothing.** On short planning calls
  the model decides a summary is not worth it. `REASONING_SUMMARY=detailed` is the
  setting that actually fills the panel.
- **Enabling the Responses API changes `.content` from `str` to a list of blocks.** Never
  read it directly — `core/llm.py` (`text_of`, `reasoning_of`) is the only correct
  accessor, and every entry point goes through it.
- **`pydantic-settings` reads `.env` into `Settings` and puts nothing in `os.environ`.** So the
  OpenAI SDK's own `AZURE_OPENAI_API_KEY` lookup finds nothing and the call dies with "Missing
  credentials" — `build_model` passes `api_key` explicitly for exactly that reason. The same
  applies to any SDK here that reads its own environment.
- **`stream_mode="messages"` carries ToolMessages and the summarizer's output too.**
  Unfiltered, `run_query`'s JSON row dump renders in the transcript as the agent's
  answer. `agent/turn.py` keeps only `AIMessageChunk` and skips anything tagged
  `COMPACTION_TAG` — the summarizing model runs inside the graph like any other call.
- **`get_stream_writer()` raises outside a runnable context**, which is how unit tests
  invoke a tool directly. It is a no-op under `ainvoke`, so a caller that invokes a tool
  that way is unaffected.
  `core.events.emit` swallows exactly that `RuntimeError`.
- **A method named `list` shadows the builtin for every annotation later in the class
  body**, and `list[dict]` then fails at import. `ChatStore.list_chats` is named that way
  for this reason.
- **One rule, one place, or it will drift.** Four of these were caught in review after
  being written twice: what re-running a step discards
  (`store.invalidated_by`, shipped to the browser as `invalidates` rather than recomputed
  there), and what a failed step record looks like (`steps._record`, written only by
  `steps.action`). Health goes over the wire structured (`Health.as_dict`) for the same
  reason — the browser was matching on the word "down". Collapsing connection management
  onto the portal alone was the same problem at a larger scale. `layout.validate` is what both the agent's `set_layout` and the browser's
  `POST /{id}/layout` go through, for the same reason.
- **`db.connect` is how both SQLite files are opened.** They are separate files on
  purpose; how they are opened is not a per-store decision, and the copy in each store
  had already drifted on `PRAGMA foreign_keys`.
- **The frontend build output is not committed.** Vite names every chunk by its hash, so two
  branches touching `src/web` always collide on `index.html` and on the asset names it
  points at — and the only correct resolution is whatever `make web-build` produces from the
  merged source, never either side. So `src/analyst_agent/web/static/` is ignored, and a
  clean clone has no frontend until it is built: `analyst` answers a 503 saying so, and
  `make web` builds and serves in one step. It still builds *into* the package, because
  `uv_build` ships only `src/analyst_agent` and a wheel needs it there — which means a wheel
  built without running `make web-build` first serves that same 503.

## Conventions

- **Tiers before packages.** A new module's home is decided by what it imports, not by what
  it is about — see **Layering** above. If it needs something from a tier above, that thing
  is passed in as an argument or moved down; `tests/test_layering.py` will not let it import
  upward. A new top-level package has to be given a tier or the test fails on it.
- **A name is taken once.** `Catalog`, `Entity`, `MemberDoc`, `Folder`, `Result`, `Event` and
  `run.py` each meant two unrelated things at once, and a reader could not tell which from
  the import. Grep before naming: `cube.catalog` owns `Catalog`/`Entity`/`MemberDoc`/`Folder`
  for the *served* model, so the generation side is `Introspection`, `Definition`,
  `MemberDescription` and `FolderDecision`. An uploaded file's shape is a third such thing and is
  `Profile`; the transcript block for what a script wrote is `files`/`.wrote` and not "artifact",
  because "artefact" already means a grounding file on the sources page.
- **No single-caller private helpers.** If a function has one call site, inline it —
  procedural flow reads better than a chain of one-use hops. Functions with two or more
  callers stay, especially where they exist to stop two code paths drifting
  (`available_members`, `exposed_members`).
- Tests cover the deterministic half only — no containers, no LLM calls. Anything
  container-dependent is verified by running it against a live source and checking the
  result, not by mocking.
- Comments explain *why*, particularly where a Cube behaviour forced the design.
- Ruff, line length 100, `select = ["E", "F", "I", "UP", "B"]`.
