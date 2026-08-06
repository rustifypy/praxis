# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install                  # uv sync --all-groups
make test                     # uv run pytest -q
make lint                     # ruff check + ruff format --check
make fmt                      # ruff format + ruff check --fix
make up / make down           # docker compose -f docker-compose.sources.yml
make logs SOURCE=<name>       # follow one source's container
make api                      # uvicorn on :8000

uv run pytest tests/test_gate.py::test_valid_query_passes    # a single test
uv run pytest tests/test_model_generation.py -q              # one file

make web                      # build the frontend into the package (needs npm)
make serve                    # analyst web — opens the portal in a browser

uv run analyst catalog                                 # what Cube currently exposes
uv run analyst ask "..." --sql                         # ask, optionally show SQL
uv run analyst web --no-browser --port 8481            # portal without opening a tab
uv run analyst check '{"measures":["x.count"]}'        # gate tiers 1-3, no execution
uv run analyst source add|ls|test|rm|sync              # manage data sources
uv run analyst source model <name> --enrich --yes      # generate a data model
```

## Three subsystems

**Query time** (`cube/`, `agent/`, `retrieval.py`, `grounding.py`) — the agent answers
questions. It never writes SQL. It emits Cube JSON queries that pass a four-tier gate
(pydantic schema → local semantic checks against the cached catalog → `POST /v1/sql`
which compiles without executing → `POST /v1/load`). Failures return as text the agent
can act on, never exceptions. See README for the tier table.

**Model generation** (`sources/model/`) — builds the Cube data model a source exposes.
Six modules, one per stage, and `run.generate` is the whole sequence in order:

| module | stage |
|---|---|
| `warehouse.py` | the vocabulary: `Column`, `Table`, `Schema` |
| `read.py` | probe container → `Schema` (keys, columns, statistics) |
| `build.py` | `Schema` → cube bodies + the menu of choices (pure, no LLM) |
| `decide.py` | LLM descriptions/measures/views, and the guards on them |
| `publish.py` | decisions → YAML, then write/restart/validate/restore |
| `run.py` | `generate()` — the ordered pipeline |

Two invariants hold throughout: **the LLM returns decisions, never YAML** (pydantic
objects via `with_structured_output`, then checked against a derived menu), and **Cube
is the validator** — a candidate model goes to the real container and Cube says what is
wrong. Do not write a YAML checker.

`publish.install` is the only way a model reaches a container: write → restart → ask.

**The web portal** (`chat/`, `web/`, `src/web/`) — `analyst web` opens a browser the way
Jupyter does. One flat event stream is the contract between agent and browser:

| module | role |
|---|---|
| `chat/events.py` | the `Event` schema and `emit()` — the wire format |
| `chat/store.py` | SQLite: `chats` + `events` + the LangGraph checkpointer, one file |
| `chat/service.py` | `stream_turn()` — one turn as an async iterator of events |
| `web/server.py` | routes, SSE, token auth, static mount |
| `src/web/` | React source; **builds into `analyst_agent/web/static/`** |

Two invariants: **the event log is the transcript** — replaying a chat replays its
events, and `Session` provenance is rebuilt from it (`replay_session`), so nothing lives
only in memory. And **tools author their own events** through LangGraph's stream writer
rather than the service inferring them from chunks, so a gate rejection reaches the
browser with its reasons attached.

`parent_id`/`depth` are on every event and always `None`/`0`. They are reserved for the
orchestrator's subagents so that adding nesting never changes the protocol.

## Sources

One Cube container per data source. `sources.yml` is the registry (no credentials);
`.env.sources/<name>.env` holds credentials at `0600`; `docker-compose.sources.yml` is
**generated** from the registry — never hand-edit it, a test asserts it stays in sync.
`CUBE_URL` selects which source the agent queries.

## Things that will cost you an hour

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
- **`.cube-probe/` is shared** between the throwaway probe model and model backups. The
  probe owns only its own subdirectory; do not delete the parent.
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
- **The frontend build output is committed** at `src/analyst_agent/web/static/`. It lives
  inside the package because `uv_build` only ships `src/analyst_agent`; anything left in
  `src/web` would be missing from a wheel and `analyst web` would serve a 503. Run
  `make web` after changing `src/web`.

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
