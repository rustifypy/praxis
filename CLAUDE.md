# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install                  # uv sync --all-groups
make test                     # uv run pytest -q — no containers, no model calls
make lint                     # ruff check + ruff format --check
make fmt                      # ruff format + ruff check --fix
make up / make down           # the shared Cube Store (Cube itself is its own container)
make logs                     # follow the Cube container

uv run pytest tests/test_gate.py::test_valid_query_passes    # a single test
uv run pytest tests/test_model_generation.py -q              # one file
uv run pytest -q -k dashboard                                # by name

make web-build                # build the frontend into the package (needs npm)
make web                      # web-build, then serve
make serve                    # analyst — opens the portal in a browser
```

**`__main__.py` is a launcher** — a printed URL and `uvicorn.run`, and **it takes no
arguments**: host, port and data directory come from settings, so `WEB_PORT=8481 uv run
analyst` is how you move it and `.env` is where it lives. `analyst`
starts the portal and nothing else. **The portal is the only surface**: add capability to
`web/sources.py` and the panel, never a second entry point beside it. Two of them means the
connection form and the four steps' confirmations get written twice. Anything headless
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
| 1 | `cube/`, `transcript.py`, `sandbox/` | core |
| 2 | `dashboards/` | core, cube |
| 3 | `sources/` | core, cube, dashboards |
| 4 | `agent/` | core, cube, transcript, dashboards, sources |
| 5 | `web/` | anything |

`core/` is settings, the database file, the event format, the chat model, the job runner and
the subprocess wrapper — **and it is a junk drawer the moment that rule is relaxed**, so the
membership test is exactly "imports nothing from this project except its siblings."

`core.llm.build_model` and `core.events.emit` are the two that make the rule load-bearing:
nearly everything needs them, so whichever package holds them is one every other package
must depend on. Put either in a feature package and that feature is imported by its peers.

Three rules keep the tiers honest when a feature wants to cross one:

- **A tool is agent vocabulary wherever the state it edits lives.** All three toolsets are in
  `agent/tools/`, importing the stores they write through.
- **What a lower tier cannot look up, it takes as an argument.** `dashboards.refresh` is
  handed a `CubeClient` and a `Catalog`; it never opens the source row. That is the whole
  reason `dashboards` and `sources` are orderable at all.
- **An import inside `if TYPE_CHECKING:` is a cycle that has not failed yet**, not a
  solution. Move the thing down a tier, or pass it in.

## Six subsystems

**Query time** (`cube/`, `agent/`, `sources/grounding.py`) — the agent answers questions
about the one connected database: `CUBE_URL` is where Cube answers and `CubeClient.local`
is the only thing that reads it. It never writes SQL. It emits Cube JSON queries that pass
a four-tier gate
(pydantic schema → local semantic checks against the cached catalog → `POST /v1/sql`
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
`cube/model/grounding/{rules,skills,certified_queries}` — so it is versioned with the model
it describes and a branch carries its own. A directory that does not exist yet grounds on
nothing rather than erroring, which is what `Grounding.is_empty` is for.

**Model generation** (`sources/modelgen/`) — builds the Cube data model a source exposes.
`modelgen` and not `model`, because "model" in this codebase is also the LLM:

| module | stage |
|---|---|
| `warehouse.py` | the vocabulary: `Column`, `Table`, `Schema` |
| `read.py` | probe container → `Introspection` (every schema: keys, columns, statistics). Not `Catalog` — that name is `cube.catalog.Catalog`, the *served* model, which is close to the opposite thing |
| `selection.py` | which tables to model, as a filter on one `Schema` |
| `build.py` | `Schema` → cube bodies + the menu of choices (pure, no LLM) |
| `decide.py` | LLM descriptions/measures/views, and the guards on them |
| `publish.py` | decisions → YAML, then write/restart/validate |
| `state.py` | what one step leaves for the next, as JSON |
| `steps.py` | the four steps, and `generate()` for all of them in order |

Three invariants hold throughout: **the LLM returns decisions, never YAML** (pydantic
objects via `with_structured_output`, then checked against a derived menu); **Cube is the
validator** — a candidate model goes to the real container and Cube says what is wrong,
so do not write a YAML checker; and **nothing survives only in memory** — the steps run
minutes or days apart, so each writes what the next needs into the `source` row.

`publish.install` is the only way a model reaches a container: write → restart → ask.

**Four steps, not one pass.** `connect` → `cubes` → `describe` → `views`, each runnable
on its own and re-runnable. **`steps.action` is the only way to run one** — a caller
chooses *which* actions and in what order, never how, so nothing can drift on the order
rule (a step needs the previous one done) or on what a failed step records.
`read` is a fifth action with no step row: it produces the catalog the picker needs, so
it must be triggerable alone, and `cubes` runs it itself when nothing is cached.

`selection` is why nothing downstream knows selection happened: `build` already tolerates
a foreign key whose target is absent (it was written that way for keys into another
schema), and a deselected table is indistinguishable from that.

**Editing & branching** (`sources/editing/{edits,impact,workspace}.py`,
`sources/vcs.py`, `agent/tools/editor.py`, `web/model_edit.py`,
`src/web/src/components/{ModelEditor,EditorChat}.tsx`) — `cube/model/` is a git repo, and the
checked-out branch is what the container serves, because the directory is a bind mount rather
than something copied in. There is no per-branch endpoint the way Cube Cloud has one:
`git checkout` on the host *is* the deploy, so this subsystem is a gate in front of that one
mount rather than a sandbox beside it.

| module | role |
|---|---|
| `sources/vcs.py` | git as a subprocess against the model directory — branches, diff, merge, and `files_at` for reading main's blobs out of `.git` without checking main out. Beside `modelgen/` and `editing/` rather than inside either: generation commits what it writes and editing branches off it |
| `edits.py` | tiers 1-2: valid YAML, then append-only cubes vs. freely-rewritable views, diffed against **main**, never against the branch's own previous state |
| `workspace.py` | the directory as a working tree: `write` and `delete`, which both the HTTP routes and the editor agent's tools go through — one path, so the two cannot disagree on what a valid edit is |
| `impact.py` | every dashboard tile and certified query, checked against the *served* catalog — not a member-diff of main against the branch, because "is what this tile reads still here" is the question that survives whatever caused the breakage |
| `agent/tools/editor.py` | the editor agent's six tools — no merge, checkout or branch-create; those restart the container and land work, so they stay buttons on the page |
| `web/model_edit.py` | the HTTP surface: branches, files, validate (tier 3), commit, merge, and the editor chat |

Two invariants:

1. **The gate runs before the write, because the write is live.** In dev mode Cube watches the
   mount and reloads in under a second, so `edits.check` is what keeps a file that will not
   compile off disk in the first place — `write_model_file` and `PUT /files/{path}` both run
   the identical two-tier check before `write_text`, and neither has another way to refuse.
2. **Impact is only ever measured against the branch actually checked out.** Only one catalog
   is served at a time, so a report computed for any other branch would describe a model
   nothing is running. `branch_diff` and `merge` both refuse to compute impact for a branch
   that is not checked out — there is no isolated endpoint to ask that question against.

Main is generated, never edited directly — `_guard`/`_on_a_branch` refuse a write there. A
branch only ever moves forward from main: no pull, no rebase, no remote at all
(`GIT_TERMINAL_PROMPT=0`), because there is nothing to sync with. A merge conflict aborts
immediately rather than leaving markers in the tree, for the same reason a stray
`grounding/*.yml` is fatal: conflict markers in the live mount are "zero cubes," arrived at
from a different direction.

**Dashboards** (`dashboards/`, `web/dashboards.py`, `src/web/src/components/{Dashboard,
DashboardNav,Tile}.tsx`) — the same machinery pointed at a question someone asks
repeatedly. A tile stores the *query*, not the rows. `dashboards/__init__.py` holds the
three invariants; read it before changing anything here:

1. **The row is the truth, and a tool event carries only intent.** A dashboard is mutable
   state and the chat's contract is that the event log *is* the transcript; both cannot
   be true of a layout the agent edits. So `agent.tools.dashboards._start`/`_end` are the only way these
   tools reach the stream and **have no parameter that could carry a layout, a tile list
   or a chart spec** — that is the enforcement, not a rule to remember. `EVENT_KEYS`
   asserts it. Undo comes from `layouts` being append-only instead.
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

`CREATE TABLE IF NOT EXISTS` does not diff: it adds a table to a live `analyst.db` and
silently skips a new *column* on one that exists. So `core.db.SCHEMA` is what a database is
created as, and `core.db.MIGRATIONS` is how one that already exists catches up —
`core.db.apply` runs both, and is what every store calls instead of `executescript`, so a
migration cannot run for one store and be skipped by another. `user_version` counts what has
been applied; a database `apply` creates is stamped as current rather than migrated, because
`SCHEMA` already builds it in its final shape and a migration dropping a column would raise
on a table that never had one. Migrations are append-only — editing one already applied runs
nowhere. `tiles.time_member` is written on every tile and read by nothing — it is there
because the feature needing it would otherwise require exactly that column. Retention is
the mirror image: a DELETE rather than a column, so the depth stays changeable — it is the
*index* that could not be added later.

Not built: promoting a tile to a `certified_queries/` file.

**Code execution** (`sandbox/`, `agent/tools/code.py`) — a Python sandbox on the *query* agent,
not an agent of its own. `sandbox/__init__.py` holds the three invariants; read it before changing
anything here.

| module | role |
|---|---|
| `sandbox/container.py` | one `docker run` and one `docker exec`, as pure argv plus lifecycle |
| `sandbox/scratch.py` | one chat's directory, the only place a model-supplied path may land, and where saved scripts, uploads and trimmed output go |
| `sandbox/profile.py` | what an uploaded file *is*, read once on arrival and kept beside it |
| `sandbox/profiler.py` | that reading, as it runs inside the container. Never imported here |
| `sandbox/image/Dockerfile` | the analysis stack, built on demand and tagged by its own digest |
| `agent/tools/code.py` | two tools: `export_result` and `run_python` |
| `web/files.py` | the HTTP surface: a file in, a file out, and taking one back |

**`--network none` is what keeps the gate load-bearing, not a second agent.** `run_query` and a
Python interpreter are in the same hands, and that is safe because the sandbox cannot reach Cube or
the warehouse — there is no route to a number that did not come through the four tiers.
`export_result` is the only way rows cross over. What this buys is the arithmetic a `CubeQuery`
cannot express — a ratio, a period delta, a share of a total — which CLAUDE.md listed as an open
gap and the agent was doing in prose.

**Two tools and not a filesystem.** The arithmetic is a dozen lines, so a script arrives whole and
its output comes back; `scratch.save_script` keeps it anyway, which is what leaves a computed
figure's provenance on disk and lets a second attempt not overwrite the first. Filtering, grouping
and totalling stay in `run_query`, where the gate checks them and pre-aggregations serve them.

**One container for the machine, not one per chat** — a deliberate choice, measured rather than
assumed: an idle container is under a megabyte (`--memory` is a cap, not a reservation), a
`docker exec` is ~65ms against ~150ms for a whole ephemeral `docker run`. The costs are that
`--memory` and `--pids-limit` are shared, so a script exhausting either kills the container for
every chat, and that a script sees the whole `workspaces` root while `export_result` is confined to
one chat's directory. Both are survivable rather than silent: the workspace is the state, so
`ensure_up` rebuilds and `run_python` says that is what happened.

**The container is disposable precisely because the workspace is the state.** Every `run_python` is
a fresh interpreter, so nothing is lost by removing the container mid-conversation — which makes
recovery from a reboot, a restart, an OOM kill or a stale mount a 150ms retry rather than a lost
session.

**`container.rebuild` runs at startup and is never awaited.** The portal warms the sandbox as a
background task, because the first ever run *builds the image* — ninety seconds, and a portal that
will not answer until an image compiles is a portal that looks broken. Measured: healthz at ~1s,
sandbox up at ~500ms, and with docker absent the portal starts anyway and logs `sandbox is not
available: docker not found on PATH`. `ensure_up` still runs before every script and an
`asyncio.Lock` serialises it, so a script arriving mid-build waits rather than racing — two callers
both finding no container is how the loser's `remove` kills the winner's.

**A file the user sends is the same workspace, and there is no `files` table.** `POST
/api/chats/{id}/files` writes into `uploads/`, `GET .../files/{path}` reads anything in the
workspace back out, and that directory *is* the record — a row could only ever describe what `ls`
already answers. Three rules hold it together:

1. **`uploads/` is what makes an upload distinguishable** from something a script wrote, so no
   state has to say which is which. `profile.saved` reads that directory and the prompt is built
   from it every turn — which is why the composer's ✕ deletes the file rather than only the chip.
2. **An upload writes no event of its own.** It happens *between* turns and would have no
   `turn_id` to belong to, and an event authored by an HTTP route would be the first. What records
   it is the **user event** of the turn it is attached to (`POST /messages` takes the paths), so a
   replayed transcript shows the file above the question that used it.
3. **A script's own files are named back to it.** `run_python` snapshots the workspace either side
   of the run and reports what changed, on the event as `wrote` and in what the model reads —
   otherwise a chart it drew is a file nobody knows exists.

**A running script can be watched and stopped, and neither goes through the container.** Output is
streamed as `output` events — **the one event type that is never stored**, because `tool_end`
already carries the whole of it trimmed to `OUTPUT_LIMIT` and storing deltas as well would put an
untrimmed second copy in the event log; `agent/turn.py` holds that exemption. Stopping is the same
shape: the flag `run_turn` watches is only tested between stream chunks, and an exec is one `await`
that no chunk interrupts, so `run_python` races the run against `session.cancel` itself. The kill
reaches *inside* — `echo $$` records the pid, the host reads it out of the bind mount, and
`container.kill` sends the TERM — because killing the `docker exec` client leaves the command
running. Measured live: dead 0.1s after the button, container still up, next script normal.

**A script is shown before it runs, and that is not a safety gate.** The sandbox is what makes a
script safe; `approve_scripts` exists because the moment before a run is the only one where the
script is readable. So `run_python` emits an `approval` event carrying the script — **the one event
that carries a body**, and it is not stored, because the file is already at `scripts/run-NNN.py`
and the outcome is on `tool_end`. The answer comes back on a queue (`POST /chats/{id}/approve`),
the same shape as the cancel flag rather than a LangGraph interrupt: a confirm should not close the
stream and resume a thread. Two consequences worth keeping: **"always" is what makes it usable** —
without it a write-run-fix loop is fifteen clicks and the setting gets turned off — and **stopping
the turn answers the question with no**, which is what stops a closed tab leaving a script waiting
on a queue nobody will put anything on.

**Three caps, and the one that was missing.** `EXEC_TIMEOUT_S` bounds a call and `OUTPUT_LIMIT`
bounds what it says, but forty model calls of each is eighty minutes and 160,000 characters — so
`TURN_SECONDS`/`TURN_OUTPUT` bound the turn, and `ToolCallLimitMiddleware` bounds the count with
`exit_behavior="continue"` so the agent answers from what it has rather than stopping dead. The
turn's numbers are provisional: no long write-run-fix loop has been measured yet.

**The profile is what makes an upload usable rather than merely present.** Without it the first
move after every upload is a `run_python` to discover the columns: it works, costs a round trip,
and comes out differently each time — and a 200 MB CSV cannot go into a context at all, so a
summary is the only safe thing to hand over whole. It is read by duckdb *in the sandbox* (`SUMMARIZE`
plus one exact `count(DISTINCT)` pass, so "this column is unique" is a fact rather than an
approximation), never raises, and a file it cannot read is a note the agent can act on rather than
a failed upload.

**`make sandbox-check` is the other half of `make test`.** The suite covers the argv, the path
resolution, the CSV export, the trimming and both file routes; what a container actually *does* —
no egress, no capabilities, a read-only root, a kill at the timeout, a script that dies on the stop
button, output that arrives before the exit, and what the profiler makes of a real CSV — is
asserted against a live one by `scripts/sandbox_check.py`, with `--stale` for the mount recovery,
`--ask` for a real turn and `--file` to attach something to it.

**The web portal** (`web/`, `src/web/`, and the transcript beneath it) — `analyst` opens a
browser the way Jupyter does. One flat event stream is the contract between agent and browser:

| module | role |
|---|---|
| `core/events.py` | the `Event` schema and `emit()` — the wire format, in `core` because every toolset writes it |
| `transcript.py` | the `chats`, `events` and `results` tables, and the LangGraph checkpointer beside them. Storage only: turning a transcript back into provenance is `agent.session.replay` |
| `web/stream.py` | `sse()` — the wire framing and the headers, for both streams |
| `agent/turn.py` | `stream_turn()` — one turn as an async iterator of events |
| `web/server.py` | routes, SSE, token auth, static mount |
| `src/web/` | React source; **builds into `analyst_agent/web/static/`** |
| `src/web/src/components/Rail.tsx` | the mode switch — chat, boards or data, and nothing else |
| `src/web/src/components/ChatHeader.tsx` | what is being queried, and a link to manage it |
| `src/web/src/components/ServedModel.tsx` | the compiled model **and** its grounding, one list |
| `src/web/src/components/ResultBlock.tsx` | one query's data: the table, the chart above it, CSV |
| `src/web/src/components/FileBlock.tsx` | what a script wrote: images inline, everything else a download |
| `src/web/src/components/Composer.tsx` | the question, and the files it arrives with |
| `src/web/src/components/ResultChart.tsx` | line / bar / kpi as inline SVG, from a spec |
| `src/web/src/components/Dashboard.tsx` | one board: the grid and **the only Refresh** |
| `src/web/src/components/Tile.tsx` | one tile: its chart or table, its window, its delta |
| `src/web/src/format.ts` | how a result value renders — cells, axis ticks, sort order |

Two invariants: **the event log is the transcript** — replaying a chat replays its
events, and `Session` provenance is rebuilt from it (`agent.session.replay`), so nothing lives
only in memory. And **tools author their own events** through LangGraph's stream writer
rather than the service inferring them from chunks, so a gate rejection reaches the
browser with its reasons attached.

`output` is the single exception to the first, and deliberately narrow: a running script's stdout,
live only, because the whole of it lands on `tool_end` trimmed and a replayed transcript reads it
from there. Any *other* wire-only type would be state that exists nowhere — check that the whole
thing is already stored somewhere before adding one.

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
`describe.py` makes for its sentence. `chart_for` returning `None` is a correct and common
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
as `wrote`, which is the `set_chart` argument again — paths only, because the file is on disk in
the bind mount and the download route is how it is read.

**The browser is separate products, so it is separate modes.** `Rail.tsx` owns the switch
and the column beside it swaps whole — `ChatNav`, `DashboardNav` or `DataNav`, never two.
Chat and data shared one sidebar until the chat list stayed live and clickable while you
were administering a database. Boards sit between them because that is the order the work
happens in: made in chat, read against the model. The mode is read off the hash and nowhere
else (`useHash`), as is which dashboard is open; a mirrored React copy disagreed with the
address bar after a back button. A hash and not a router — three views do not justify one.

**Data mode has no middle column** (`.app.no-nav`), because there is one connection and a
sidebar listing it would be a list of one. Chat and boards keep theirs.

`ChatHeader` is where "what am I querying" lives: the chip carries the database's name, the
health dot, the branch when it is not main, and `servingNote` — the size of the model, which
is what says it is *built* and not merely connected. The chip is a **link to the data page
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

`SourceDetail` shows **five** stages where `store.STEPS` has four: `read` writes no step
row, but hiding it inside "Build cubes" meant expanding the second of four steps offered
a button that did something else. `store.STAGES` is the one definition of the sequence and
`store.stages` is the one place that knows `read`'s state comes from the cached catalog
rather than from `steps`; the browser iterates the rail it ships on `SourceRow.stages`.

## Storage

**One file, one schema.** `core.db.SCHEMA` is every table this process creates, and
`~/.analyst/analyst.db` holds all of them: `chats` and `events` (the transcript),
`results`, `dashboards`/`tiles`/`layouts`/`runs`/`tile_results`, `source`, and
LangGraph's `checkpoints`/`writes` — those last two created by `AsyncSqliteSaver` rather
than here. `core.db.connect` is how it is opened, so `PRAGMA foreign_keys` cannot drift between
the three stores that open it.

The schema lives in `core/db.py` rather than beside each store because **the unit is the file,
not the store**: `ChatStore`, `DashboardStore` and `SourceStore` all write it and none owns
it, and a schema split across three modules reads as three databases.

The file is **chmod 600** — `SourceStore.init` sets it, because `source.credentials` is
plaintext JSON and the file mode is the whole guard.

One person on one machine: the token guard is the whole access story, and
`cube/client.py`'s `LOCAL_CLAIMS` is the single literal signed into the Cube JWT.

**A file is not a row.** An uploaded CSV, a chart a script drew and a file's profile all live in
`DATA_DIR/workspaces/<chat>/`, and none of them has a table. That directory is bind-mounted into
the sandbox, so a row describing it could only ever be a second copy of `ls` — and the copy is
what would go stale. Deleting a chat takes the workspace with it (`scratch.remove`).

## The data source

**One Cube deployment.** One container, one model directory, and **one row in `source`** —
credentials included, with `id = 1` enforced by a CHECK rather than by convention.
`sources/docker.py` starts the container with `docker run -e` built from that row, so there
is nothing on disk that can drift from it: no registry file, no credential file, no
generated compose file, and do not add one. `docker-compose.yml` is hand-written and holds
only the shared Cube Store, on an explicitly named `analyst-net` network the container joins.

That row is a table in `analyst.db` like everything else — chats, dashboards, the source and
the checkpointer in one file under `DATA_DIR`. So `DATA_DIR` scopes the whole install rather
than just the history: a scratch directory is a scratch *everything*, connection included.

**There is one address, and it is `CUBE_URL`.** `settings.api_root` builds it, `CubeClient.local`
is the only thing that reads it, and `settings.cube_port` derives the published port from it so
docker and the query path cannot name different numbers. Nothing stores a second copy — a chat,
a dashboard and a tile all query wherever `CUBE_URL` points, because there is nowhere else.

The container name and the model path are **constants, not stored values** —
`docker.CONTAINER` is `cube-analyst` and `registry.MODEL_ROOT` is `cube/model`. Nothing
selects them, so nothing can disagree about which container or which directory is meant.

**Connecting is a replacement, not an addition.** `SourceStore.save` is `INSERT OR REPLACE`
and resets `steps` and `state` with it: those describe the tables of a database that is no
longer connected, and keeping them would offer a "Build cubes" that rewrites the model from
a catalog nothing read.

**A chat has a `kind`.** `'question'` runs the query agent, `'model'` the editor agent;
both share this table and the transcript format and differ only there. `list_chats`
filters on it, so the sidebar shows question chats and the model editor shows its own.

`core/jobs.py` runs **one job at a time, machine-wide**, guarded by a `flock` — the
probe container has one name, so a second portal process must wait rather than delete it
underneath the first. A job is not stored: it lives in memory, streams to whoever is watching, and
writes its outcome onto the source row's `steps` as it goes. Refreshing the browser
re-attaches (the buffer replays); restarting the server loses the narration, not the
result.

`registry.model_dir()` is also a git repo once `vcs.ensure` has touched it — see **Editing &
branching** above for what that buys and what it costs (one container, so one checked-out
branch, at a time).

## Things that will cost you an hour

- **`python-multipart` is a separate dependency and `UploadFile` fails at *request* time
  without it.** Not at import, so the upload route looks like a working route until the first
  file — `import multipart` raising `ModuleNotFoundError` is the whole diagnosis.
- **`read_csv_auto` will read a PDF and report a shape for it.** duckdb sniffs anything as one
  column of nonsense rather than refusing, so `profiler.view` matches named extensions and raises
  for the rest. A confident wrong profile is worse than "not a tabular file".
- **A literal `../` never reaches a route — every client collapses it before it is sent.** So
  `GET /files/../../analyst.db` arrives as `/api/analyst.db` and answers 405, and a test written
  that way proves nothing. The guard cannot be the URL's shape; it is `scratch.resolve` on the
  *resolved* target, and the test has to percent-encode (`%2e%2e%2f`) to reach it at all.
- **A file dropped anywhere but the drop target is *navigated to*, replacing the page.** So the
  composer's listeners are on `window` rather than on itself — it only exists in the chat view, so
  that is the right scope — and a drop lands in the composer wherever it was released.
- **A run's new files cannot be a set difference of names.** A script that re-draws `chart.png`
  adds no name, and the run whose picture is on screen would report having written nothing.
  `scratch.snapshot` keys on `(size, mtime)` for exactly that.
- **`get_stream_writer()` fails two different ways, and a unit test hid the second.** With
  no config at all it raises the documented `RuntimeError`. Calling a tool's own
  `.ainvoke()` outside a graph produces a config that exists but holds no runtime, and the
  lookup dies with `KeyError('__pregel_runtime')` — which sailed through the bare
  `except RuntimeError` in `core/events.py` and killed the call. Fourteen passing tests
  missed it because the ones that assert on events **monkeypatch `emit`**, so the real
  no-op path had no coverage until a script drove `add_tile` directly. When a fixture
  replaces the thing under test, add one case without the fixture.
- **The sandbox image has no `ps`, `pkill`, `pgrep` or `/bin/kill`** — `python:3.12-slim` carries
  no procps, and `kill` exists only as a bash builtin. So `docker exec <name> kill …` fails, and
  fails *expensively*: docker reports a missing executable as `OCI runtime exec failed`, which is
  the string `EXEC_START_FAILED` matches, so `execute` would read it as a stale mount and rebuild
  the shared container — taking every other chat's running script with it. `kill_argv` goes through
  `bash -c`, and the pid comes from a file the run writes rather than from a process search.
  Adding procps instead would change the Dockerfile's digest, and `image_tag()` is that digest, so
  every install rebuilds the image on next start.
- **`StreamReader.readline` raises past 64 KiB**, so a streaming reader has to take chunks: one
  `print` of a wide dataframe has no newline in it and would fail rather than stream.
- **`docker exec` reports "OCI runtime exec failed" on *stdout*, with exit 127.** Not stderr.
  A recovery branch keyed on `result.err` matches nothing and silently never runs — which is
  how `container.execute`'s stale-mount retry passed review and did nothing until it was driven
  against a real container. Check `result.out + result.err` for anything docker says about the
  exec itself, as opposed to what the command printed.
- **A running container's bind mount survives the host directory being deleted, and points at
  the dead inode.** `DATA_DIR/workspaces` removed and recreated leaves the container up and
  healthy by every check `docker inspect` can answer, while every `exec` into it fails to
  `chdir`. So `ensure_up` returning early on "running" is not sufficient, and the recovery has
  to `remove()` before calling it — `ensure_up` would otherwise find the broken container
  healthy and hand back the same mount.
- **`httpx` transport errors do not subclass `OSError`.** An `except OSError` around a Cube
  call catches nothing useful — a plain `ConnectError` went straight through and 500'd the
  whole dashboard page for a stopped container, which is the exact failure that branch
  existed to prevent. The established pair here is `(CubeError, httpx.RequestError)`; grep
  for an existing except-clause before writing a new one.
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
  `web/sources.py` are both derived from it. Without it a view and a cube are two entities
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
- **Cube Core *does* hot-reload model files when `CUBEJS_DEV_MODE=true`** — which is how every
  container here runs. A new `views/x.yml` was served in under a second with no restart,
  and a file with a bad `join_path` took the whole model to a compile error just as fast, then
  recovered the moment it was deleted. Two consequences: a save is **live**, so the edit gate refusing bad YAML *before* the
  write is what keeps Cube up, not the restart afterwards; and a restart is still what
  `publish.install` and a branch checkout use, because a whole model changing at once should be
  compiled from a known state rather than from whatever the watcher saw mid-write.
- **Cube qualifies a single-identifier `sql:` but passes an expression through verbatim.**
  `sql: unit_price` becomes `"invoice_line".unit_price`; `sql: quantity * unit_price`
  reaches the database unqualified and dies with `column reference "unit_price" is
  ambiguous` as soon as a join brings in another table with that column. Bare columns are
  written bare (matching Cube's own generator); multi-column expressions are qualified
  with `{CUBE}` by `decide.qualify_expression`.
- **`CUBEJS_DEV_MODE=true` disables JWT verification** and is what serves
  `/playground/db-schema`. Fine locally; never for anything real.
- **A database on this machine is `host.docker.internal` from inside a container**, not
  `localhost`.
- **Escape model/Cube/database text before `console.print`.** Rich reads `[...]` as
  markup and silently swallowed the gate's `[rule-id]` prefixes. Use `rich.markup.escape`.
- **`cube/model/` is the only thing written into the working directory.** That
  directory belongs to whoever installed this — it is a dependency, and their `cube/` is
  theirs to version. Everything else goes under `DATA_DIR`
  (`~/.analyst`): `analyst.db`, the job lock, and `probe-model/` (the catalog probe's
  throwaway cubes, cleared on every read). Do not put scratch — a probe directory, a model
  backup — into the project. Note the probe directory is **bind-mounted**, which is why it is
  under `~/.analyst` rather than a system tempdir — Docker Desktop's default file sharing
  covers `/Users` reliably and `/var/folders` not always.
- **`publish.write` unlinks every `*.yml` before writing, so re-running `cubes` destroys
  the views and descriptions.** There is no undo in the code: **the model directory is
  the user's, and version control is the only way back.** Do not add a snapshot/restore
  pair: one-deep, silently overwritten, and strictly worse than a `git diff` that shows what
  the rebuild actually changed. The picker warns before a run
  that discards later steps; committing first is on whoever runs it. This also means a
  `cubes` build Cube rejects leaves the rejected YAML on disk rather than rolling
  back, which is the right trade only because the directory is versioned — if you ever
  point this at an untracked model directory, there is nothing to recover with.
- **A container's environment cannot be changed.** Editing credentials means `docker rm`
  plus `docker run`, which is what the `connect` step does; `restart` would keep the old
  password. The same applies to `CUBEJS_DEV_MODE` and `CUBEJS_API_SECRET`.
- **`docker run --network` needs a network that already exists.** Nothing else creates one,
  so `docker.ensure_network` runs before every create. Compose
  prefixes container names with its project (`analyst-ds-cubestore-router-1`) but the
  service name is the alias on the network, which is what the container resolves — so a
  `--filter name=^...$` anchored on the service name matches nothing.
- **Cube's port is published on `127.0.0.1` only.** `CUBEJS_DEV_MODE=true` disables JWT
  verification, and a `0.0.0.0` bind would answer the whole network unauthenticated.
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
  `/api/results/{id}/csv` — the same lesson as `/sources/{name}/{action}`, one layer down.
- **`ROWS` is a SQLite keyword**, so the `results` table stores its rows in a column named
  `data` — which is also what `events` calls its JSON blob.
- **Editing `core.db.SCHEMA` alone changes new databases only.** `CREATE TABLE IF NOT EXISTS`
  does not diff, so a new *table* appears on an existing `analyst.db` and a new *column*
  silently does not — and nothing at all is ever removed. Anything an existing file has to
  lose is a `core.db.MIGRATIONS` entry as well as a `SCHEMA` edit. Every INSERT still names
  its columns, so that a schema edit is a loud error rather than values shifted one place
  left.
- **New CSS goes on new class names — check `styles.css` first.** `.dot` is the rail's brand
  dot; reusing a taken name for the sources panel inherits `display: flex` and a monospace
  font, and every card lays out sideways. The trap is that a name may be claimed by markup
  rather than by CSS, so grepping the stylesheet alone will not find it.
- **A FastAPI path parameter as broad as `/sources/{name}/{action}` swallows its
  siblings**, so `/sources/{name}/read` never matched. Container actions live under
  `/container/{action}`.
- **`CUBE_API_SECRET` and `CUBEJS_API_SECRET` are one secret with two names**, and HS256
  needs at least 32 bytes. `mint_token` refuses to sign below that rather than let pyjwt
  print `InsecureKeyLengthWarning` on every request. Rotating it means editing both keys
  in `.env` *and* `make down && make up` — compose interpolates `CUBEJS_API_SECRET` from
  `.env` at container start, so the container keeps the old key until it is recreated.
  `CUBEJS_DEV_MODE=true` skips JWT verification, so a mismatch will not surface locally;
  it fails the moment dev mode goes off.
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
  being written twice: which connection fields are required (`drivers.missing_fields`, run
  by the route and shipped to the form as `required_envs`), what re-running a step discards
  (`store.invalidated_by`, shipped to the browser as `invalidates` rather than recomputed
  there), and what a failed step record looks like (`steps._record`, written only by
  `steps.action`). Health goes over the wire structured (`Health.as_dict`) for the same
  reason — the browser was matching on the word "down". Collapsing connection management
  onto the portal alone was the same problem at a larger scale. `drivers.split_mounts` is
  another: connect and edit both have to turn a typed host path into a mount plus a
  container path, and only one of them getting it right is a key file Cube cannot find.
  `layout.validate` is what both the agent's `set_layout` and the browser's
  `POST /{id}/layout` go through, for the same reason.
- **A bind mount whose host path does not exist is not an error — docker creates an empty
  directory there.** For a key file that means Cube opens a folder and fails somewhere far
  away. `web.sources._paths` stats the file and 400s while the person is still looking at
  the form, which is the only cheap moment to catch the typo.
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
