# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install                  # uv sync --all-groups
make test                     # uv run pytest -q — no containers, no model calls
make lint                     # ruff check + ruff format --check
make fmt                      # ruff format + ruff check --fix
make up / make down           # the shared Cube Store (sources are their own containers)
make logs SOURCE=<name>       # follow one source's container
make api                      # uvicorn on :8000

uv run pytest tests/test_gate.py::test_valid_query_passes    # a single test
uv run pytest tests/test_model_generation.py -q              # one file
uv run pytest -q -k dashboard                                # by name

make web-build                # build the frontend into the package (needs npm)
make web                      # web-build, then serve
make serve                    # analyst — opens the portal in a browser

uv run analyst --no-browser --port 8481                # portal without opening a tab
```

**`__main__.py` is a launcher** — argparse, three flags, `uvicorn.run` — and `analyst`
starts the portal and nothing else. **The portal is the only surface**: add capability to
`web/sources.py` and the panel, never a second entry point beside it. Two of them means the
connection form and the four steps' confirmations get written twice, and anything resolving
its target through `settings.api_root` — the single global `CUBE_URL` — cannot survive more
than one Cube container without a `--source` flag and a `SourceStore` lookup a launcher has
no reason to hold.

`typer` and `rich` are deliberately absent from the dependency list — nothing here needs
either. That is why the launcher prints with `print(..., flush=True)` rather than
`console.print`: stdout is block-buffered when it is not a tty, so a redirected log would
hold the token in the buffer for the life of the server.

The launcher cannot be replaced by pointing uvicorn at the app. `web.server.TOKEN` is
minted at import and nothing else announces it, so `uvicorn analyst_agent.web.server:app`
serves a portal nobody can authenticate against.

## Five subsystems

**Query time** (`cube/`, `agent/`, `retrieval.py`, `grounding.py`) — the agent answers
questions about **one** source: `CUBE_URL` picks it, `grounding.for_active_source` is
the only place that resolves it, and its grounding is whatever that source carries.
It never writes SQL. It emits Cube JSON queries that pass a four-tier gate
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
`extra="forbid"` with no `ungrouped`, so a ratio or a period delta cannot be expressed and
the model does that arithmetic in prose today. Deterministic post-processing over rows that
already passed the gate is the way to close it without giving up the gate.

Grounding is **per source and has no global layer** — it names members, so one shared
set is wrong the moment there are two warehouses, and the agent was being handed
`revenue.net_revenue` definitions for a database with no revenue cube. It lives at
`cube/sources/<name>/grounding/{rules,skills,certified_queries}`. A `CUBE_URL` with no
source row behind it grounds on nothing rather than erroring, which is what
`Grounding.is_empty` is for.

**Model generation** (`sources/model/`) — builds the Cube data model a source exposes:

| module | stage |
|---|---|
| `warehouse.py` | the vocabulary: `Column`, `Table`, `Schema` |
| `read.py` | probe container → `Catalog` (every schema: keys, columns, statistics) |
| `selection.py` | which tables to model, as a filter on one `Schema` |
| `build.py` | `Schema` → cube bodies + the menu of choices (pure, no LLM) |
| `decide.py` | LLM descriptions/measures/views, and the guards on them |
| `publish.py` | decisions → YAML, then write/restart/validate |
| `state.py` | what one step leaves for the next, as JSON |
| `run.py` | the four steps, and `generate()` for all of them in order |

Three invariants hold throughout: **the LLM returns decisions, never YAML** (pydantic
objects via `with_structured_output`, then checked against a derived menu); **Cube is the
validator** — a candidate model goes to the real container and Cube says what is wrong,
so do not write a YAML checker; and **nothing survives only in memory** — the steps run
minutes or days apart, so each writes what the next needs into `sources.db`.

`publish.install` is the only way a model reaches a container: write → restart → ask.

**Four steps, not one pass.** `connect` → `cubes` → `describe` → `views`, each runnable
on its own and re-runnable. **`run.action` is the only way to run one** — a caller
chooses *which* actions and in what order, never how, so nothing can drift on the order
rule (a step needs the previous one done) or on what a failed step records.
`read` is a fifth action with no step row: it produces the catalog the picker needs, so
it must be triggerable alone, and `cubes` runs it itself when nothing is cached.

`selection` is why nothing downstream knows selection happened: `build` already tolerates
a foreign key whose target is absent (it was written that way for keys into another
schema), and a deselected table is indistinguishable from that.

**Editing & branching** (`sources/model/{edits,impact,vcs,tools}.py`, `web/model_edit.py`,
`src/web/src/components/{ModelEditor,EditorChat}.tsx`) — a source's model directory is a git
repo, and the checked-out branch is what the container serves, because the directory is a bind
mount rather than something copied in. There is no per-branch endpoint the way Cube Cloud has
one: `git checkout` on the host *is* the deploy, so this subsystem is a gate in front of that
one mount rather than a sandbox beside it.

| module | role |
|---|---|
| `vcs.py` | git as a subprocess against the model directory — branches, diff, merge, and `files_at` for reading main's blobs out of `.git` without checking main out |
| `edits.py` | tiers 1-2: valid YAML, then append-only cubes vs. freely-rewritable views, diffed against **main**, never against the branch's own previous state |
| `impact.py` | every dashboard tile and certified query bound to the source, checked against the *served* catalog — not a member-diff of main against the branch, because "is what this tile reads still here" is the question that survives whatever caused the breakage |
| `tools.py` | the editor agent's six tools — no merge, checkout or branch-create; those restart the container and land work, so they stay buttons on the page |
| `web/model_edit.py` | the HTTP surface: branches, files, validate (tier 3), commit, merge, and the editor chat |

Two invariants:

1. **The gate runs before the write, because the write is live.** In dev mode Cube watches the
   mount and reloads in under a second, so `edits.check` is what keeps a file that will not
   compile off disk in the first place — `write_model_file` and `PUT /files/{path}` both run
   the identical two-tier check before `write_text`, and neither has another way to refuse.
2. **Impact is only ever measured against the branch actually checked out.** Two sources can
   both carry a member of the same name, and a number from the wrong warehouse would look
   exactly as plausible as a right one. `branch_diff` and `merge` both refuse to compute impact
   for any branch that is not the one checked out — there is no isolated endpoint to ask a
   not-checked-out branch's question against.

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
   be true of a layout the agent edits. So `tools._start`/`_end` are the only way these
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
| `run.py` | `validate` (tiers 1-3) and `refresh` (+ tier 4), the atomic pass |
| `delta.py` | change since the previous refresh, single-row results only |
| `insights.py` | one model call per refresh for the whole board, never per tile |
| `tools.py` | the eight agent tools — which do **not** include refreshing |
| `web/dashboards.py` | the HTTP surface; refresh lives here and nowhere else |

**The agent authors and arranges; the page runs.** Refresh is a button, deliberately: an
agent that could trigger one would turn "tidy up this dashboard" into two dozen warehouse
queries. The consequence to state rather than let someone discover — a tile the agent just
added has **no data** until someone refreshes, and the system prompt tells it to say so.

Two failure states, kept apart because only one is fixable by editing a tile: `refused` is
tiles that did not compile and nothing ran; `failed` is tiles that compiled and then broke
against the warehouse. A dashboard renders its last `ok` run, so neither blanks the tiles.

`tiles.CHART_TYPES` is `line|bar|kpi|table` — exactly what `ResultChart` draws. `area`,
`stacked_bar` and `hbar` are absent on purpose: a validator that accepts a chart the
renderer cannot draw makes the tile misrepresent itself. **Renderer branch first, tuple
second.** (`tools.add_tile`'s docstring still advertises the wider list to the model; the
validator refuses it, so the failure is a rejection rather than a wrong picture.)

`store.py` will **never gain a column** — `CREATE TABLE IF NOT EXISTS` does not diff, so
adding a table to a live `analyst.db` works and adding a column silently does not. Every
column nothing reads yet (`tiles.time_member`, `tiles.deleted_at`, `tile_results.insight`)
is there because the feature needing it would otherwise require deleting the database.
Retention is the mirror image: a DELETE rather than a column, so the depth stays
changeable — it is the *index* that could not be added later.

Not built, each gated so nothing can author one prematurely: the three missing chart types
above; **dragging tiles** (`POST /{id}/layout` exists and validates, no UI calls it, so
rearranging is chat-only); per-tile insights (`tile_results.insight`, rejected as twelve
model calls for a weaker result than the one cross-tile paragraph); promoting a tile to a
`certified_queries/` file.

**The web portal** (`chat/`, `web/`, `src/web/`) — `analyst` opens a browser the way
Jupyter does. One flat event stream is the contract between agent and browser:

| module | role |
|---|---|
| `chat/events.py` | the `Event` schema and `emit()` — the wire format |
| `chat/store.py` | SQLite: `chats` + `events` + the LangGraph checkpointer, one file |
| `web/stream.py` | `sse()` — the wire framing and the headers, for both streams |
| `chat/service.py` | `stream_turn()` — one turn as an async iterator of events |
| `web/server.py` | routes, SSE, token auth, static mount |
| `src/web/` | React source; **builds into `analyst_agent/web/static/`** |
| `src/web/src/components/Rail.tsx` | the mode switch — chat, boards or data, and nothing else |
| `src/web/src/components/ChatHeader.tsx` | the active source, and switching it — nothing deeper |
| `src/web/src/components/ServedModel.tsx` | the compiled model **and** this source's grounding, one list |
| `src/web/src/components/ResultBlock.tsx` | one query's data: the table, the chart above it, CSV |
| `src/web/src/components/ResultChart.tsx` | line / bar / kpi as inline SVG, from a spec |
| `src/web/src/components/Dashboard.tsx` | one board: the grid, the insight, and **the only Refresh** |
| `src/web/src/components/Tile.tsx` | one tile: its chart or table, its window, its delta |
| `src/web/src/format.ts` | how a result value renders — cells, axis ticks, sort order |

Two invariants: **the event log is the transcript** — replaying a chat replays its
events, and `Session` provenance is rebuilt from it (`replay_session`), so nothing lives
only in memory. And **tools author their own events** through LangGraph's stream writer
rather than the service inferring them from chunks, so a gate rejection reaches the
browser with its reasons attached.

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

*An answer owns the results of its turn.* Tool events arrive before the prose, so
`events.ts` lifts every `result` item from the turn in progress to sit after the new
answer. Scoped to the turn, not to the tail: the agent reasons *after* reading a result, so
items arrive as `[… tool, result, thinking, text]` and a rule that only moved trailing
results left the table above the answer. The trace stays above; only data moves down, and a
turn that produced no answer keeps its results where they are rather than hiding them.

**The browser is separate products, so it is separate modes.** `Rail.tsx` owns the switch
and the column beside it swaps whole — `ChatNav`, `DashboardNav` or `DataNav`, never two.
Chat and sources shared one sidebar until the chat list stayed live and clickable while you
were administering a database. Boards sit between them because that is the order the work
happens in: made in chat, bound to a source. The mode is read off the hash and nowhere else
(`useHash`), as is which dashboard is open; a mirrored React copy disagreed with the address
bar after a back button. A hash and not a router — three views do not justify one.

`ChatHeader` is where "which source am I querying" lives: the chip carries the name, the
health dot and `servingSummary` — the size of the model, which is what says a source is
*built* and not merely connected — and its panel is a source **switcher and nothing
else**. It briefly hosted `ServedModel` too, on the reasoning that "what can I ask about"
is a question you have with a cursor in the composer. That was the wrong depth for a
dropdown: clicking the chip to check which database you were on opened an admin screen
over the transcript. `ServedModel` lives on the source page only, one deliberate click
away via "Manage sources"; the panel is sized to its own longest row now, not to that
component.

`ServedModel` is **master–detail, not accordions**: the entity list is a fixed column and
members render in the box beside it, because expanding in place pushed every row below
down the page. The list stays in name order even while filtering — only the *default
selection* follows the best match, since re-ranking rows under someone who is typing moves
what they are reaching for.

**Its sections are collapsed by default** and there is no selection on arrival. Five
sections open at once is a column of eighty rows; shut, the shape of a whole source fits
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
badly). Four early returns used to hide grounding for reasons that had nothing to do with
it; they are the sections' empty copy now.

The default selection stays on the model when there is one — an artefact only opens when
nothing else survives the filter, or when there is no model at all.

`SourceDetail` shows **five** stages where `store.STEPS` has four: `read` writes no step
row, but hiding it inside "Build cubes" meant expanding the second of four steps offered
a button that did something else. `STAGES` in `src/web/src/sources.ts` is the UI's list
and `run.generate`'s own order; `stageState` is the one place that knows `read`'s state
comes from the cached catalog rather than from `steps`.

## Sources

One Cube container per data source, and **one row in `sources.db` is the whole source** —
credentials included. `sources/docker.py` starts each container with `docker run -e` built
from that row, so there is nothing on disk that can drift from it: no registry file, no
per-source credential file, no generated compose file. All three existed before and were
removed with `sources/compose.py` and the one-time importer. `docker-compose.yml` is hand-written and
holds only the shared Cube Store, on an explicitly named `analyst-net` network that
standalone containers join.

`sources.db` lives at `SOURCES_DB` (`~/.analyst/sources.db`), deliberately **not** under
`DATA_DIR`: pointing `--data-dir` at a scratch file to protect chat history must not hide
the sources. `CUBE_URL` selects which source the agent queries — exactly one is "active",
and the registry has no say in it.

`sources/jobs.py` runs **one job at a time, machine-wide**, guarded by a `flock` — the
probe container has one name, so a second portal process must wait rather than delete it
underneath the first. A job is not stored: it lives in memory, streams to whoever is watching, and
writes its outcome onto the source's `steps` as it goes. Refreshing the browser
re-attaches (the buffer replays); restarting the server loses the narration, not the
result.

`source.model_dir()` is also a git repo once `vcs.ensure` has touched it — see **Editing &
branching** above for what that buys and what it costs (one container, so one checked-out
branch, at a time).

## Things that will cost you an hour

- **`get_stream_writer()` fails two different ways, and a unit test hid the second.** With
  no config at all it raises the documented `RuntimeError`. Calling a tool's own
  `.ainvoke()` outside a graph produces a config that exists but holds no runtime, and the
  lookup dies with `KeyError('__pregel_runtime')` — which sailed through the bare
  `except RuntimeError` in `chat/events.py` and killed the call. Fourteen passing tests
  missed it because the ones that assert on events **monkeypatch `emit`**, so the real
  no-op path had no coverage until a script drove `add_tile` directly. When a fixture
  replaces the thing under test, add one case without the fixture.
- **`httpx` transport errors do not subclass `OSError`.** An `except OSError` around a Cube
  call catches nothing useful — a plain `ConnectError` went straight through and 500'd the
  whole dashboard page for a stopped container, which is the exact failure that branch
  existed to prevent. The established pair here is `(CubeError, httpx.RequestError)`; grep
  for an existing except-clause before writing a new one.
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
- **A head slice of a time series is the *oldest* rows.** `tiles.insight_slice` samples
  across the whole span and forces the final bucket in, because an insight model handed
  head-thirty will confidently describe a trend that has since reversed — the `MAX_POINTS`
  lesson one layer up, with the model as the victim instead of the chart.
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
  `views/`.** Verified on a live source: a `grounding/certified_queries/probe.yml` took
  chinook from 13 cubes to **0**, with `Compile errors: ... Unexpected YAML key: name.
  Only 'cubes', 'views', and 'view_groups' are allowed here` — the error names the
  grounding file, and every query against that source is down until it goes. A `.md` in
  the same place is ignored entirely, which is why grounding is markdown and why
  `_load_dir` globs `*.md`. There is no top-level Cube key for a saved query, so no
  "Cube-compliant" YAML can hold one; the only free-form slot is `meta:`, which
  `publish.write` regenerates. If grounding ever needs a structured format, that is a
  decision to make grounding *generated* — not a way to keep hand-written files.
- **Cube Core *does* hot-reload model files when `CUBEJS_DEV_MODE=true`** — which is how every
  source container here runs. A new `views/x.yml` was served in under a second with no restart,
  and a file with a bad `join_path` took the whole source to a compile error just as fast, then
  recovered the moment it was deleted. (This bullet previously claimed the opposite. It was
  wrong.) Two consequences: a save is **live**, so the edit gate refusing bad YAML *before* the
  write is what keeps a source up, not the restart afterwards; and a restart is still what
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
- **`cube/sources/` is the only thing written into the working directory.** That
  directory belongs to whoever installed this — it is a dependency, and their `cube/` is
  theirs to version. Everything else machine-local goes under
  `~/.analyst`: `sources.db`, the job lock, and `probe-model/` (the catalog probe's
  throwaway cubes, cleared on every read). There used to be a `.cube-probe/` in the
  project root holding the probe and model backups; both are gone. Do not put scratch
  back into the project. Note the probe directory is **bind-mounted**, which is why it is
  under `~/.analyst` rather than a system tempdir — Docker Desktop's default file sharing
  covers `/Users` reliably and `/var/folders` not always.
- **`publish.write` unlinks every `*.yml` before writing, so re-running `cubes` destroys
  the views and descriptions.** There is no undo in the code: **the model directory is
  the user's, and version control is the only way back.** A snapshot/restore pair existed
  and was deliberately removed — one-deep, silently overwritten, and strictly worse than
  a `git diff` that shows what the rebuild actually changed. The picker warns before a run
  that discards later steps; committing first is on whoever runs it. This also means a
  `cubes` build Cube rejects now leaves the rejected YAML on disk rather than rolling
  back, which is the right trade only because the directory is versioned — if you ever
  point this at an untracked model directory, there is nothing to recover with.
- **A container's environment cannot be changed.** Editing credentials means `docker rm`
  plus `docker run`, which is what the `connect` step does; `restart` would keep the old
  password. The same applies to `CUBEJS_DEV_MODE` and `CUBEJS_API_SECRET`.
- **`docker run --network` needs a network that already exists.** Compose used to create
  one; nothing does now, so `docker.ensure_network` runs before every create. Compose
  prefixes container names with its project (`analyst-ds-cubestore-router-1`) but the
  service name is the alias on the network, which is what sources resolve — so a
  `--filter name=^...$` anchored on the service name matches nothing.
- **Source ports are published on `127.0.0.1` only.** `CUBEJS_DEV_MODE=true` disables JWT
  verification, and a `0.0.0.0` bind would answer the whole network unauthenticated.
- **A progress sink may return a value.** `run._say` tests `inspect.isawaitable` rather
  than truthiness: the web's sink returns the `Event` it emitted, and awaiting that killed
  every job on its first progress call.
- **`body { line-height: 1.6 }` is inherited by buttons but not by inputs**, so identical
  padding gives a 32px button beside a 27px input beside a 27px icon button. Every control
  that shares a row takes `min-height: var(--control)` and `line-height: 1.2`; do not size
  a new one with padding alone.
- **`.container button` also matches buttons inside components nested in that container.**
  `.detail-primary button` was restyling the overflow menu's items into bordered grey
  boxes, because it is later in the file than `.menu-panel button` at equal specificity.
  Use `>` when a container holds a child component that renders its own controls.
- **`overflow-y: auto` computes `overflow-x` to `auto` as well.** An absolutely positioned
  popover wider than such a container does not spill out of it — it makes that container
  scroll sideways, and the content is unreachable. `Info` is `position: fixed` and placed
  from `getBoundingClientRect`, which is what makes it safe inside the sources list column
  and the chat header's scrolling panel. It closes on scroll because a fixed panel does
  not travel with its anchor.
- **`.panel` is in the DOM with no rule behind it.** `ToolCall.tsx` renders
  `<div className="panel">` and `styles.css` defines only `--panel` (a colour token),
  `.panel-head` and `.panel-toggle`. Defining `.panel` retroactively restyles the tool
  trace. This is the `.source`/`.dot` trap already sprung — the name is taken by markup
  rather than by CSS, so grepping the stylesheet alone will not find it. (`.panel-head` is
  also defined twice at equal specificity, so the sources-page copy wins on `gap` and
  leaks a `margin-bottom` into the transcript.)
- **`.answer.streaming > :last-child::after` is the streaming caret**, so anything appended
  as the last child of `.answer` grows a blinking cursor. A result block is a sibling of
  `.answer` inside `.row`, never a child. Relatedly, `.answer-tools` reserves its height
  while invisible; harmless when an answer ended the transcript, but it opened a ~38px hole
  once a result followed one — hence the `:has()` rule pulling `.res` up under an answer.
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
  `data` — which is also what `events` calls its JSON blob. Adding a *table* to `SCHEMA`
  works on an existing `analyst.db`; adding a *column* silently does not, because
  `CREATE TABLE IF NOT EXISTS` does not diff and there is no migration mechanism.
- **New CSS goes on new class names — check `styles.css` first.** `.source` and `.dot`
  were already taken by the old sidebar footer and the brand dot; reusing them for the
  sources panel inherited `display: flex` and a monospace font, and every card laid out
  sideways. `.source` is gone with that footer, `.dot` is now the rail's brand, and the
  rule that caught it still applies.
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
  `use_responses_api`. `model.py` refuses to build rather than silently serving an empty
  thinking panel.
- **`reasoning: {"summary": "auto"}` frequently emits nothing.** On short planning calls
  the model decides a summary is not worth it. `REASONING_SUMMARY=detailed` is the
  setting that actually fills the panel.
- **Enabling the Responses API changes `.content` from `str` to a list of blocks.** Never
  read it directly — `agent/content.py` (`text_of`, `reasoning_of`) is the only correct
  accessor, and every entry point goes through it.
- **`stream_mode="messages"` carries ToolMessages and the summarizer's output too.**
  Unfiltered, `run_query`'s JSON row dump renders in the transcript as the agent's
  answer. `service.py` keeps only `AIMessageChunk` and skips anything tagged
  `COMPACTION_TAG` — the summarizing model runs inside the graph like any other call.
- **`get_stream_writer()` raises outside a runnable context**, which is how unit tests
  invoke a tool directly. It is a no-op under `ainvoke`, so a caller that invokes a tool
  that way is unaffected.
  `chat.events.emit` swallows exactly that `RuntimeError`.
- **A method named `list` shadows the builtin for every annotation later in the class
  body**, and `list[dict]` then fails at import. `ChatStore.list_chats` is named that way
  for this reason.
- **One rule, one place, or it will drift.** Four of these were caught in review after
  being written twice: what a valid new source is (`registry.validate_new`), what
  re-running a step discards (`store.invalidated_by`, shipped to the browser as
  `invalidates` rather than recomputed there), which source is active
  (`Source.is_active`), and what a failed step record looks like (`run._record`, written
  only by `run.action`). Health goes over the wire structured (`Health.as_dict`) for the
  same reason — the browser was matching on the word "down". Collapsing source management
  onto the portal alone was the same problem at a larger scale. `drivers.split_mounts` is
  the newest one: create and edit both have to turn a typed host path into a mount plus a
  container path, and only one of them getting it right is a key file Cube cannot find.
  Dashboards add two: `layout.validate` is what both the agent's `set_layout` and the
  browser's `POST /{id}/layout` go through, and `run.source_mismatch` is the only thing
  standing between a saved query and a number from the wrong warehouse — two sources can
  both have `sales.revenue`, nothing downstream would error, and the tile would render a
  plausible figure about a different database.
- **A bind mount whose host path does not exist is not an error — docker creates an empty
  directory there.** For a key file that means Cube opens a folder and fails somewhere far
  away. `web.sources._paths` stats the file and 400s while the person is still looking at
  the form, which is the only cheap moment to catch the typo.
- **`db.connect` is how both SQLite files are opened.** They are separate files on
  purpose; how they are opened is not a per-store decision, and the copy in each store
  had already drifted on `PRAGMA foreign_keys`.
- **The frontend build output is committed** at `src/analyst_agent/web/static/`. It lives
  inside the package because `uv_build` only ships `src/analyst_agent`; anything left in
  `src/web` would be missing from a wheel and `analyst` would serve a 503. Run
  `make web-build` after changing `src/web`.

## Conventions

- **No single-caller private helpers.** If a function has one call site, inline it —
  procedural flow reads better than a chain of one-use hops. Functions with two or more
  callers stay, especially where they exist to stop two code paths drifting
  (`available_members`, `exposed_members`).
- Tests cover the deterministic half only — no containers, no LLM calls. Anything
  container-dependent is verified by running it against a live source and checking the
  result, not by mocking.
- Comments explain *why*, particularly where a Cube behaviour forced the design.
- Ruff, line length 100, `select = ["E", "F", "I", "UP", "B"]`.
