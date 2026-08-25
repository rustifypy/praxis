# analyst-agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Contributions welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)

An analytics agent over a self-hosted Cube Core semantic layer.

The agent never writes SQL. It emits **Cube JSON queries**, which pass a four-tier
gate before anything touches the warehouse. Cube compiles the query to SQL and
executes it; the agent only ever speaks HTTP to Cube and holds no database
credentials.

- **Chat** — ask questions in plain English, get a governed query, a table, a chart, and the trace
- **Boards** — pin questions asked repeatedly as dashboard tiles that store the query, not the rows
- **Data** — connect a warehouse, generate its Cube model in four steps, then edit that model on a branch

Everything above is one portal, one process, one machine. See [Contributing](#contributing)
if you want to help build the parts in [Next](#next).

## Why JSON queries and not SQL

A JSON query is a closed grammar — measures, dimensions, typed filter operators.
It cannot express an ungoverned column, an arbitrary join, or a hand-rolled
metric. An LLM writing SQL fails silently with a plausible wrong number; an LLM
writing JSON queries fails loudly with a fixable error.

## The gate

| Tier | What | Cost |
|---|---|---|
| 1 | Pydantic schema (`modelstore/schema.py`) | free, local |
| 2 | Member existence, operator/type match, WHERE-vs-HAVING rule, granularity, scope (`modelstore/gate.py`) | free, local |
| 3 | `POST /v1/sql` — Cube compiles to SQL, executes nothing | 1 HTTP call, no warehouse cost |
| 4 | `POST /v1/load` — execute with row and time caps | warehouse |

Every failure returns as text the agent can act on, never an exception. Tier 2
rejections include the nearest matching member names, so the agent corrects on
the next turn instead of flailing.

## Quick start

```bash
cp .env.example .env          # Azure OpenAI key + endpoint, check QUERY_MODEL
make install
make web                      # build the frontend, then open the portal
```

Then: **Data** in the rail → Connect a database. The portal hands you a compose file per
service; save each one anywhere, `docker compose up -d`, and paste the addresses back.
Then walk the four stages, and the Model panel shows what Cube compiled. Switch to **Chat** and
ask questions; the header there names the database you are querying and how much of it is
modelled. **Boards** sits between the two — pin a question you'll ask again and it becomes a
tile.

There is no bundled database — bring your own. A database running on this machine is
`host.docker.internal` from inside the container, not `localhost`.

`cubejs/cubestore:latest` is amd64 only, so on Apple Silicon set `CUBESTORE_TAG=arm64v8` in
that file — the generated one carries it as a commented line. Without it the pull fails with
`no matching manifest for linux/arm64/v8` rather than emulating.

## The web portal

```bash
make web-build          # build the frontend once (needs npm; output is committed)
uv run analyst          # opens a browser, Jupyter-style, with a token in the URL
```

Multiple chats, each with its own history. Answers stream, and so does the model's
reasoning — the panel fills while the agent plans, then collapses when it answers.

Every step shows up as a line you can expand: which members `search_model` found, the
Cube JSON that went to the gate, the SQL Cube generated, and the rejections in between.
Tool labels are derived from the query itself, so the trace can never claim a query that
did not run.

The **data** is not in that trace. Each query's rows render as their own block below the
answer they support — a formatted, sortable table with a chart above it where the query's
shape suits one, and CSV beside it. Both the header and the chart are derived from the
query in Python (`modelstore/shape.py`, `modelstore/visualize.py`), so they are free, testable, and
cannot describe a column nobody fetched; **no chart is a normal outcome** and means the
table is the better answer. Full result sets live in a `results` table keyed by a handle,
which is what `Show all` and the CSV read.

Bound to `127.0.0.1` with a token generated per launch. It is one person on one machine
— there is no login and no user table.

Reasoning summaries need `AZURE_OPENAI_API_VERSION=2025-03-01-preview` or later;
`analyst` refuses to start below it rather than showing an empty thinking panel.

The frontend source is `src/web/` (React + Vite). `npm run dev` there gives hot reload
against a running `analyst`. `make web-build` is what produces what ships.

## Storage

One SQLite file, `~/.analyst/analyst.db`, holds all of it: chats and the flat event stream
that *is* the transcript, full result sets, dashboards and their tiles and refresh history,
the connection with its credentials, and the LangGraph checkpointer that gives a chat its
memory. `DATA_DIR` moves the lot, so pointing it at a scratch directory gives you a scratch
*everything* — including an unconnected database.

The file is chmod 600, because the database credentials in it are plaintext.

There is no migration mechanism, deliberately. `CREATE TABLE IF NOT EXISTS` does not diff,
so adding a table to an existing database works and adding a column silently does not —
changing a column means deleting the file and starting again. That is an affordable answer
for one person on one machine and a bad one for anything else; it is the first thing to
revisit if this ever runs somewhere shared.

## Dashboards

A dashboard is the same machinery pointed at a question someone asks repeatedly. A tile
stores the **query**, not the rows — so a dashboard costs nothing to keep around and
nothing goes stale silently.

The agent authors and arranges tiles from chat ("put yesterday's revenue next to this
board" adds a tile); it never refreshes one. **Refresh is a button on the page**,
deliberately — an agent that could trigger a refresh would turn "tidy up this dashboard"
into a dozen warehouse queries nobody asked for. A newly-added tile has no data until
someone clicks it, and the agent says so rather than implying otherwise.

A refresh is atomic: every tile compiles before any tile executes, so one broken tile
can't leave the rest half-updated, and the same compile pass runs when a board opens —
a member a model rebuild dropped shows up as a banner, not a silent gap. A refresh also
**re-executes rather than rewrites**: a tile asking for "last 30 days" moves with every
refresh, one asking for `2026-01-01..2026-01-31` never does, and the tile labels which
kind it is from what Cube actually resolved.

## The deployment

**The portal runs nothing at all.** It holds the credentials, mints the secrets, and renders a
compose file; you run each deployment wherever you want it and tell the portal its address.

The semantic layer is one of them — one compose file, three published images:

| container | what it is |
|---|---|
| `modelstore` | the data model — files, refs and history, as a git repo behind an API — **and the gateway**: it serves Cube and signs its tokens. Built from its own repository |
| `cube-api` | stock `cubejs/cube`, with `cube.py` supplied through compose `configs`. One process serves every branch at once |
| `cubestore` | Cube's queue and cache driver. Production mode refuses to query without it |

The same file serves a laptop and a server, because the portal only ever talks to **one** URL
here: the store's. **Cube's port is not published** — queries go through the store at
`/refs/{branch}/query`, which mints a token naming the branch *and* its revision, so
`CUBEJS_API_SECRET` never leaves that compose file and there is exactly one authenticated door
in. Nothing runs in dev mode, so there is no port here that must never be exposed. The code
sandbox is a separate deployment, optional and sharing nothing with this one — its own published
image, released from its own repository like the store.

**One deployment, one row in the `deployment` table** — `id = 1` is a CHECK, not a convention —
holding the credentials, the API secret and the store's token. `deployenv` is the one place
that decides what a deployment needs, and it renders the hand-over with the values substituted
and every image pinned — the pin is the only compatibility control there is, because the store
reports no version handshake. There is no `.env`, and nothing is written to disk.

Connecting is two steps, because the portal cannot do the middle one:

1. **Store the connection.** Credentials in, secrets minted, nothing running.
2. **Paste the addresses.** Cube's and the store's, as this machine reaches them.

That second step is also how you follow a deployment that moved, so there is one path rather
than two. Connecting a *different* database replaces the first and takes the recorded steps with
it — they describe a database that is no longer there. The model itself stays in the store,
history and all.

The connection is managed from the portal — **Data** in the rail — and only from there: the
deployment panel, a stepper for the four stages with one panel open at a time, a connection
test, a **Model** panel showing what Cube compiled from `/v1/meta`, and a danger zone.

### Four stages

Building a model is four stages rather than one pass, because a person chooses tables in the
middle of it and because the expensive part deserves its own progress. There is no Connect
stage: connecting is a form, and a stage records something that *ran*.

| stage | what it does |
|---|---|
| **Read tables** | introspect every schema at once and cache it |
| **Build cubes** | pick tables, emit one cube each with keys and joins, install |
| **Describe with AI** | one model call per table: descriptions, measures, `ai_context` |
| **Build views** | views over the join paths, validated across every join |

Each is runnable and re-runnable on its own — what one needs from the last is stored on the
deployment row, not held in memory. `steps.action` is the single entry point, so the order rule
and what a failure records live in one place. The catalog read covers **every** schema at once,
so choosing tables afterwards is instant and offline, and ticking a fact table pulls in what it
references. That matters: describing is one model call per table, so four hundred tables is an
hour of them.

**Reading the catalog needs no container.** The probe cubes are committed to a `probe` ref, the
connected Cube is asked about that ref, and the ref is deleted. Tables and columns come from
`information_schema`, so it works against a production-mode Cube on someone else's machine.

**A rebuild cannot lose the model it replaces.** Re-running a stage writes the whole model to a
`candidate` ref, asks Cube about it *there*, and only then moves `main` — so a model Cube
refuses never replaces the one being served, and stays readable at `candidate`. The stage before
it is the parent commit, and `GET /diff` shows what changed. The portal still warns first:
recoverable is not the same as intended.

One job runs at a time, machine-wide, guarded by a `flock` — two portal processes rebuilding one
model store would race on the same ref.

**A model change is live without a restart.** Cube asks the store for the version of the ref it
is serving on every request, so a commit is picked up within seconds. That is why the edit gate
below runs before the write and not after.

### Editing the model

> **Being rebuilt.** This part was written when the model was a directory on this machine and
> `git checkout` was the deploy. The model now lives in the store and Cube pulls it per ref, so
> the editor still writes to a local directory that nothing serves. It is the unfinished half of
> that move, not a bug to work around.

The design it is moving to: the model store is a git repo, and every ref is servable at once —
one Cube compiles each independently — so editing happens on a ref of its own, never on `main`,
which the pipeline above generates.

Create a branch, then either edit YAML files directly or open the editor chat and describe the
change. Both paths run through the same two-tier gate before anything is committed — cubes are
**append-only** (add measures/dimensions/segments, edit `description`/`title`/`meta`/`format`,
never rename or remove what's there), views are freely rewritable. **Validate** asks Cube what
compiled on that ref and checks every dashboard tile against what is actually being served. **Merge** does the same check once more, then folds the ref into `main` —
refused if it would break a saved query, unless you force it.

There's no remote and no PR review inside the tool: no pull, no rebase, nothing to sync with.

`ungrouped` is absent from `CubeQuery` on purpose. It would return raw
un-aggregated rows and void the aggregate-only guarantee.

## Commands

```bash
uv run analyst                                          # the chat portal
make web                                                # build the frontend, then serve
make web-build                                          # the frontend artifact alone
make test lint
```

`analyst` only starts the portal, and takes no arguments — host, port and data directory
come from the environment or `.env`. It cannot be replaced by pointing uvicorn at the app:
the access token is minted at import and nothing else prints it.

`src/web/` is the frontend source and sits outside the Python package deliberately. Its
build output does not: `uv_build` ships only `src/analyst_agent`, so Vite writes to
`src/analyst_agent/web/static/`. That directory is **not** committed — hashed chunk names
make it conflict on every branch — so a fresh clone has no frontend until you run
`make web-build`, and `analyst` answers a 503 until you do. `make web` does both.

## Next

- Add the eval harness: question -> golden JSON query, graded on result sets
  (sort-invariant, 4 significant figures, column-name agnostic). This is next, not last:
  everything below changes what the agent answers, and there is currently no way to tell
  whether a prompt or tool change helped.
- Search dimension *values*, not just member names. The gate checks that
  `sales.billing_country` exists and never that `"USA"` is a value it holds, so a wrong
  literal comes back as "No data matched" and the agent either retries blind or reports
  zero. A `search_values(member, text)` tool over a `dimensions: [member]` query closes it
  with no vector store.
- Pin a result as a saved report.
- Click a bar to drill: `drillMembers` is on `/v1/meta` measures and unparsed. Note a drill
  query uses `measureFilter`, which pre-aggregations cannot serve.
- Swap `LexicalRetriever` for pgvector once the model outgrows fuzzy matching.
- Add the orchestrator and subagents once single-agent accuracy is measured.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup, the test/lint loop, and what to
verify against a real container before opening a PR — the test suite deliberately skips
anything that talks to a database or a model. Everyone participating is expected to follow
the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
