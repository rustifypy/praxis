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
make web                      # build the frontend, then open the portal
```

Then: **Data** in the rail → Add source → walk the five stages, and the Serving panel on
that source's page shows what Cube compiled. Switch to **Chat** and ask questions; the
header there names the source you are querying and lists everything it exposes.

There is no bundled database — bring your own. Adding it in the portal starts its
Cube container and verifies the connection. A database running on this machine is
`host.docker.internal` from inside the container, not `localhost`.

Cube Store has no multi-arch `latest` tag. `.env.example` sets
`CUBESTORE_TAG=arm64v8` for Apple Silicon — change it to `latest` on Intel or
AMD, or the containers fail with `no matching manifest for linux/amd64`.

## The web portal

```bash
make web-build          # build the frontend once (needs npm; output is committed)
uv run analyst          # opens a browser, Jupyter-style, with a token in the URL
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
`analyst` refuses to start below it rather than showing an empty thinking panel.

The frontend source is `src/web/` (React + Vite). `npm run dev` there gives hot reload
against a running `analyst`. `make web-build` is what produces what ships.

## Data sources

Each source is one Cube container with its own model directory. **One row in
`sources.db` is the whole source**, credentials included — there is no registry file, no
credential file, and no generated compose file. `sources/docker.py` builds a `docker run`
from that row, which is why adding a source is one INSERT and one command, and why nothing
on disk can fall out of sync with it. The trade is that the credentials are visible to
`docker inspect`; the database is chmod 600 and the ports are bound to `127.0.0.1`.

`docker-compose.yml` is hand-written and holds only the shared Cube Store, on a network
named `analyst-net` that each source container joins.

Sources are managed from the portal — **Data** in the rail — and only from there. Each
source has its own page: a stepper for the five stages with one panel open at a time, the
container controls behind an overflow menu, a connection test, a **Serving** panel showing
what Cube compiled from `/v1/meta`, and a danger zone. There is no CLI for any of it: one
connection form and one validator are easier to keep honest than two, and the model stages
need the progress stream a terminal cannot show as well.

### Four steps

Building a model is four steps rather than one pass, because a person chooses tables in
the middle of it and because the expensive part deserves its own progress. The catalog
read is a fifth action with no step row — the portal shows it as its own stage, since
"read the tables" and "build cubes from the tables you picked" are not one decision:

| step | what it does |
|---|---|
| **Connect** | create the container from the row, wait for `/readyz` |
| **Build cubes** | read every schema once, pick tables, emit cubes, install |
| **Describe with AI** | one model call per table: descriptions, measures, `ai_context` |
| **Build views** | views over the join paths, validated across every join |

Each is runnable and re-runnable on its own — what one step needs from the last is stored
on the source, not held in memory. `run.action` is the single entry point, so the order
rule and what a failure records live in one place. The catalog read covers **every**
schema in one throwaway container, so choosing tables afterwards is instant and offline,
and ticking a fact table pulls in what it references. That matters: describing is one
model call per table, so four hundred tables is an hour of them.

Re-running an earlier step rewrites every model file, views included. The portal asks
first; there is no undo in the tool. `cube/sources/<name>/` is yours to version-control,
and `git` is the way back — with a diff of what the rebuild changed, which a one-deep
backup could never show you.

One job runs at a time, machine-wide: the probe container has one name, so two model
generations would destroy each other. The lock is a `flock`, so a second portal process
waits rather than corrupting the first.

`CUBE_URL` selects the source the agent queries — the portal marks exactly one **active**
and every other one *configured, not connected*. Cube's playground is on that same port.

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
  web/static/          committed frontend build (make web-build)
  agent/model.py       QUERY_MODEL -> chat model (Azure endpoint/deployment wiring)
  agent/build.py       create_agent wiring, caller identity for the audit log
  sources/drivers.py   connection shape per Cube driver
  sources/store.py     sources.db — the registry, credentials, steps, cached catalog
  sources/registry.py  the Source dataclass, model dirs, name and port rules
  sources/docker.py    docker run/stop/logs per source, all async
  sources/jobs.py      one job at a time: the lock, the buffer, the watchers
  sources/health.py    connection test via Cube's /readyz
  web/sources.py       the sources HTTP surface and the job stream — the only
                       way a source is created, edited or run
  web/stream.py        sse() — one framing for the chat and the job streams
  db.py                how this process opens SQLite, for both stores
  api.py  __main__.py  entrypoints

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
uv run analyst                                          # the chat portal
make web                                                # build the frontend, then serve
make web-build                                          # the frontend artifact alone
make api                                                # POST /ask on :8000
make test lint
```

`analyst` is the whole CLI, and it only starts the portal. `catalog`, `ask` and `check` were removed: each resolved
its target through the single global `CUBE_URL`, which does not survive more than one
Cube container. The portal selects a source; the CLI never did.

`src/web/` is the frontend source and sits outside the Python package deliberately. Its
build output does not: `uv_build` ships only `src/analyst_agent`, so Vite writes to
`src/analyst_agent/web/static/` and that directory is committed.

## Next

- Swap `LexicalRetriever` for pgvector once the model outgrows fuzzy matching.
- Add the eval harness: question -> golden JSON query, graded on result sets
  (sort-invariant, 4 significant figures, column-name agnostic).
- Add the orchestrator and subagents once single-agent accuracy is measured.
