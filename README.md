# analyst-agent

An analytics agent over a self-hosted Cube Core semantic layer.

The agent never writes SQL. It emits **Cube JSON queries**, which pass a four-tier
gate before anything touches the warehouse. Cube compiles the query to SQL and
executes it; the agent only ever speaks HTTP to Cube and holds no database
credentials.

## Why JSON queries and not SQL

A JSON query is a closed grammar — measures, dimensions, typed filter operators.
It cannot express an ungoverned column, an arbitrary join, or a hand-rolled
metric. An LLM writing SQL fails silently with a plausible wrong number; an LLM
writing JSON queries fails loudly with a fixable error.

## The gate

| Tier | What | Cost |
|---|---|---|
| 1 | Pydantic schema (`cube/schema.py`) | free, local |
| 2 | Member existence, operator/type match, WHERE-vs-HAVING rule, granularity, scope (`cube/gate.py`) | free, local |
| 3 | `POST /v1/sql` — Cube compiles to SQL, executes nothing | 1 HTTP call, no warehouse cost |
| 4 | `POST /v1/load` — execute with row and time caps | warehouse |

Every failure returns as text the agent can act on, never an exception. Tier 2
rejections include the nearest matching member names, so the agent corrects on
the next turn instead of flailing.

## Quick start

```bash
cp .env.example .env          # add Azure OpenAI key + endpoint, check QUERY_MODEL
make install
uv run analyst source add     # point it at your database
make catalog                  # confirm Cube is serving the model
uv run analyst ask "net revenue by month for the first half of 2026" --sql
```

There is no bundled database — bring your own. `analyst source add` starts the
Cube container for it and verifies the connection. A database running on this
machine is `host.docker.internal` from inside the container, not `localhost`.

Cube Store has no multi-arch `latest` tag. `.env.example` sets
`CUBESTORE_TAG=arm64v8` for Apple Silicon — change it to `latest` on Intel or
AMD, or the containers fail with `no matching manifest for linux/amd64`.

## The web portal

```bash
make web                # build the frontend once (needs npm; output is committed)
uv run analyst web      # opens a browser, Jupyter-style, with a token in the URL
```

Multiple chats, each with its own history, kept in `~/.analyst/analyst.db` along with
the LangGraph checkpointer that gives a chat its memory. Answers stream, and so does the
model's reasoning — the panel fills while the agent plans, then collapses when it
answers.

Every step shows up as a line you can expand: which members `search_model` found, the
Cube JSON that went to the gate, the SQL Cube generated, the rows that came back, and
the rejections in between. Tool labels are derived from the query itself, so the trace
can never claim a query that did not run.

Bound to `127.0.0.1` with a token generated per launch. It is one person on one machine
— there is no login and no user table.

Reasoning summaries need `AZURE_OPENAI_API_VERSION=2025-03-01-preview` or later;
`analyst web` refuses to start below it rather than showing an empty thinking panel.

The frontend source is `src/web/` (React + Vite). `npm run dev` there gives hot reload
against a running `analyst web`. `make web` is what produces what ships.

## Data sources

Each source is one Cube container with its own model directory and its own
credentials. `sources.yml` is the registry; `docker-compose.sources.yml` is
generated from it.

```bash
uv run analyst source add            # interactive: driver, credentials, port
uv run analyst source ls --check     # probes each source's /readyz
uv run analyst source rm <name>
```

Adding a source writes `.env.sources/<name>.env` (chmod 600, git-ignored), an
empty `cube/sources/<name>/`, and a regenerated compose file — then starts the
container and verifies the connection through Cube's own `/readyz`.

`CUBE_URL` selects the source the agent queries; ports are listed in
`sources.yml`. Cube's playground is on that same port.

Cube Core does not hot-reload model files — writing YAML into
`cube/sources/<name>/` takes effect on the next restart of that one container.

## Layout

```
src/analyst_agent/
  config.py            settings
  cube/schema.py       CubeQuery — the agent's only data-access output format
  cube/catalog.py      /v1/meta -> members with valid_operators + granularities
  cube/client.py       httpx client, JWT minting
  cube/gate.py         the four tiers
  retrieval.py         model search (lexical; swap for pgvector)
  grounding.py         rules / skills / certified queries loader
  agent/tools.py       search_model, run_query, load_grounding, explain_last_query
  agent/content.py     text_of / reasoning_of — content is blocks, not a string
  chat/events.py       the Event schema and emit(); parent_id/depth reserved
  chat/store.py        SQLite chats + events + LangGraph checkpointer, one file
  chat/service.py      stream_turn() — one turn as a stream of events
  cube/describe.py     deterministic one-line labels for the tool trace
  web/server.py        portal routes, SSE, token auth
  web/static/          committed frontend build (make web)
  agent/model.py       QUERY_MODEL -> chat model (Azure endpoint/deployment wiring)
  agent/build.py       create_agent wiring, caller identity for the audit log
  sources/drivers.py   connection shape per Cube driver
  sources/registry.py  sources.yml, credential files, model dirs
  sources/compose.py   renders docker-compose.sources.yml
  sources/health.py    connection test via Cube's /readyz
  sources/cli.py       analyst source add|ls|test|rm|sync
  api.py  cli.py       entrypoints

cube/sources/<name>/   one Cube data model per source, mounted at /cube/conf/model
grounding/             git-backed agent grounding
```

`ungrouped` is absent from `CubeQuery` on purpose. It would return raw
un-aggregated rows and void the aggregate-only guarantee.

## Grounding

Markdown with frontmatter, mirroring Cube's own `agents/` convention:

- `grounding/rules/` — `type: always` loads every turn; `type: agent_requested`
  loads on demand via `load_grounding`
- `grounding/skills/` — reusable workflows, progressive disclosure by description
- `grounding/certified_queries/` — canonical JSON queries, surfaced by
  `search_model` when the question resembles the `user_request` pattern

Edit these instead of the system prompt. They are reviewed diffs in git.

## Commands

```bash
uv run analyst catalog                                  # what Cube is exposing
uv run analyst ask "..." --sql                          # ask, optionally show SQL
uv run analyst check '{"measures":["revenue.count"]}'   # tiers 1-3, no execution
uv run analyst web                                      # the chat portal
make api                                                # POST /ask on :8000
make web                                                # build the frontend
make test lint
```

`src/web/` is the frontend source and sits outside the Python package deliberately. Its
build output does not: `uv_build` ships only `src/analyst_agent`, so Vite writes to
`src/analyst_agent/web/static/` and that directory is committed.

## Next

- Swap `LexicalRetriever` for pgvector once the model outgrows fuzzy matching.
- Add the eval harness: question -> golden JSON query, graded on result sets
  (sort-invariant, 4 significant figures, column-name agnostic).
- Add the orchestrator and subagents once single-agent accuracy is measured.
