# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install                  # uv sync --all-groups
make test                     # uv run pytest -q
make lint                     # ruff check + ruff format --check
make fmt                      # ruff format + ruff check --fix
make up / make down           # the shared Cube Store (sources are their own containers)
make logs SOURCE=<name>       # follow one source's container
make api                      # uvicorn on :8000

uv run pytest tests/test_gate.py::test_valid_query_passes    # a single test
uv run pytest tests/test_model_generation.py -q              # one file

make web-build                # build the frontend into the package (needs npm)
make web                      # web-build, then serve
make serve                    # analyst — opens the portal in a browser

uv run analyst --no-browser --port 8481                # portal without opening a tab
```

**There is no CLI.** `__main__.py` is a launcher — argparse, three flags, `uvicorn.run` —
and `analyst` starts the portal and nothing else. `cli.py` held `catalog`, `ask` and
`check`; all three were deleted, as `analyst source` was before them, but for a different
reason worth keeping straight. `source` went because it was a *second implementation* of
the connection form and its confirmations. The query commands were thin wrappers over the
same code the web uses, and went because each resolved its target through
`settings.api_root` — the single global `CUBE_URL` — so none survives more than one Cube
container without a `--source` flag and a `SourceStore` lookup a launcher has no reason to
hold. Add capability to `web/sources.py` and the panel; do not reintroduce a CLI.

Deleting `cli.py` dropped `typer` and `rich` from the dependency list — nothing else used
either. That is why the launcher prints with `print(..., flush=True)` rather than
`console.print`: stdout is block-buffered when it is not a tty, so a redirected log would
hold the token in the buffer for the life of the server.

The launcher cannot be replaced by pointing uvicorn at the app. `web.server.TOKEN` is
minted at import and nothing else announces it, so `uvicorn analyst_agent.web.server:app`
serves a portal nobody can authenticate against.

## Three subsystems

**Query time** (`cube/`, `agent/`, `retrieval.py`, `grounding.py`) — the agent answers
questions. It never writes SQL. It emits Cube JSON queries that pass a four-tier gate
(pydantic schema → local semantic checks against the cached catalog → `POST /v1/sql`
which compiles without executing → `POST /v1/load`). Failures return as text the agent
can act on, never exceptions. See README for the tier table.

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
| `src/web/src/components/Rail.tsx` | the mode switch — chat or data, and nothing else |
| `src/web/src/components/ChatHeader.tsx` | the active source, and what it can be asked |

Two invariants: **the event log is the transcript** — replaying a chat replays its
events, and `Session` provenance is rebuilt from it (`replay_session`), so nothing lives
only in memory. And **tools author their own events** through LangGraph's stream writer
rather than the service inferring them from chunks, so a gate rejection reaches the
browser with its reasons attached.

`parent_id`/`depth` are on every event and always `None`/`0`. They are reserved for the
orchestrator's subagents so that adding nesting never changes the protocol.

**The browser is two products, so it is two modes.** `Rail.tsx` owns the switch and the
column beside it swaps whole — `ChatNav` or `DataNav`, never both. They shared one sidebar
until the chat list stayed live and clickable while you were administering a database.
The mode is read off the hash and nowhere else (`useHash`); a mirrored React copy
disagreed with the address bar after a back button.

`ChatHeader` is where "which source am I querying" lives, and its panel hosts
`ServedModel` — that component answers *what can I ask about*, which is a question you
have with a cursor in the composer, not one worth navigating to an admin page for. The
same component still renders on the source page; it takes a `ServedModel` and a refresh
callback and knows nothing about where it is mounted. It is **master–detail, not
accordions**: the entity list is a fixed column and members render in the box beside it,
because expanding in place pushed every row below down the page. The list stays in name
order even while filtering — only the *default selection* follows the best match, since
re-ranking rows under someone who is typing moves what they are reaching for.

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

## Things that will cost you an hour

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
- **Cube Core does not hot-reload model files.** Writing YAML has no effect until that
  container restarts (~6s). Verified empirically; the docs' hot-reload refers to Cube Cloud.
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
  directory belongs to whoever installed this — it is a dependency, and their `cube/` and
  `grounding/` are theirs to version. Everything else machine-local goes under
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
  invoke a tool directly. It is a no-op under `ainvoke`, so the CLI is unaffected.
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
  same reason — the browser was matching on the word "down". Deleting the `analyst
  source` CLI was the same problem at a larger scale. `drivers.split_mounts` is the newest
  one: create and edit both have to turn a typed host path into a mount plus a container
  path, and only one of them getting it right is a key file Cube cannot find.
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
