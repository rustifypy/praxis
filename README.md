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

Then: **Data** in the rail → Connect a database → walk the five stages, and the Model
panel shows what Cube compiled. Switch to **Chat** and ask questions; the header there
names the database you are querying and how much of it is modelled. **Boards** sits
between the two — pin a question you'll ask again and it becomes a tile.

There is no bundled database — bring your own. Connecting it in the portal starts the
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

Multiple chats, each with its own history. Answers stream, and so does the model's
reasoning — the panel fills while the agent plans, then collapses when it answers.

Every step shows up as a line you can expand: which members `search_model` found, the
Cube JSON that went to the gate, the SQL Cube generated, and the rejections in between.
Tool labels are derived from the query itself, so the trace can never claim a query that
did not run.

The **data** is not in that trace. Each query's rows render as their own block below the
answer they support — a formatted, sortable table with a chart above it where the query's
shape suits one, and CSV beside it. Both the header and the chart are derived from the
query in Python (`cube/shape.py`, `cube/visualize.py`), so they are free, testable, and
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

## The data source

**One Cube deployment.** One container, one model directory, one row in the `source`
table — `id = 1` is a CHECK, not a convention — and credentials are in that row, so there
is no registry file, no credential file, and no generated compose file.
`sources/docker.py` builds a `docker run` from it, which is why connecting a database is
one INSERT and one command, and why nothing on disk can fall out of sync with it. The
address is `CUBE_URL` and nothing derives another. The trade is that the credentials are
visible to `docker inspect`; the database file is chmod 600 and the port is bound to
`127.0.0.1`.

`docker-compose.yml` is hand-written and holds only the shared Cube Store, on a network
named `analyst-net` that the Cube container joins.

The connection is managed from the portal — **Data** in the rail — and only from there:
a stepper for the five stages with one panel open at a time, the container controls behind
an overflow menu, a connection test, a **Model** panel showing what Cube compiled from
`/v1/meta`, and a danger zone. One connection form and one validator are easier to keep
honest than two, and the model stages need the progress stream a terminal cannot show as
well.

Connecting a second database *replaces* the first, and takes the recorded steps with it —
they describe a database that is no longer there. The model directory is version-controlled,
so what was built is still recoverable from git.

### Four steps

Building a model is four steps rather than one pass, because a person chooses tables in
the middle of it and because the expensive part deserves its own progress. The catalog
read is a fifth action with no step row — the portal shows it as its own stage, since
"read the tables" and "build cubes from the tables you picked" are not one decision:

| step | what it does |
|---|---|
| **Connect** | create the container from the connection, wait for `/readyz` |
| **Build cubes** | read every schema once, pick tables, emit cubes, install |
| **Describe with AI** | one model call per table: descriptions, measures, `ai_context` |
| **Build views** | views over the join paths, validated across every join |

Each is runnable and re-runnable on its own — what one step needs from the last is stored
in the source row, not held in memory. `steps.action` is the single entry point, so the
order rule and what a failure records live in one place. The catalog read covers **every**
schema in one throwaway container, so choosing tables afterwards is instant and offline,
and ticking a fact table pulls in what it references. That matters: describing is one
model call per table, so four hundred tables is an hour of them.

Re-running an earlier step rewrites every model file, views included. The portal asks
first; there is no undo in the tool. `cube/model/` is yours to version-control, and `git`
is the way back — with a diff of what the rebuild changed, which a one-deep backup could
never show you.

One job runs at a time, machine-wide: the probe container has one name, so two model
generations would destroy each other. The lock is a `flock`, so a second portal process
waits rather than corrupting the first.

Cube's playground is on the same port as the API.

Cube Core runs in dev mode here, and **does** hot-reload model files: a saved YAML change
is served in under a second, no restart. That is why the edit gate below runs before the
write and not after — a file that will not compile takes the whole model down the instant
it lands.

### Editing the model

`cube/model/` is a git repo. The checked-out branch is what the container serves — there's
no separate dev endpoint, `git checkout` on the host *is* the deploy — so editing happens
on a branch, never on `main`, which the pipeline above generates and nothing else writes
to.

From the data page: create a branch, then either edit YAML files directly or open the
editor chat and describe the change. Both paths run through the same two-tier gate before
anything reaches disk — cubes are **append-only** (add measures/dimensions/segments, edit
`description`/`title`/`meta`/`format`, never rename or remove what's there), views are
freely rewritable. **Validate** restarts the container, asks Cube what compiled, and checks
every dashboard tile and certified query against what's actually being served. **Merge**
does the same check one more time, then folds the branch into `main` and restarts —
refused if it would break a saved query, unless you force it.

There's no remote and no PR review inside the tool: no pull, no rebase, nothing to sync
with. Review happens the way it would for any git branch — `git diff`, `git log`, in your
own editor, on the same directory the container is reading.

`ungrouped` is absent from `CubeQuery` on purpose. It would return raw
un-aggregated rows and void the aggregate-only guarantee.

## Grounding

Markdown with frontmatter, mirroring Cube's own `agents/` convention, under
`cube/model/grounding/`:

- `rules/` — `type: always` loads every turn; `type: agent_requested` loads on
  demand via `load_grounding`
- `skills/` — reusable workflows, progressive disclosure by description
- `certified_queries/` — canonical JSON queries, surfaced by `search_model` when
  the question resembles the `user_request` pattern

Edit these instead of the system prompt. They are reviewed diffs in git.

**Grounding lives beside the cubes it names**, inside the same directory, so it is
versioned with the model it describes and a branch carries its own. An empty directory
is a real state: the agent then answers from the system prompt alone.

**Markdown only.** The directory is inside the tree Cube mounts as its model, and
Cube compiles every `*.yml` it finds anywhere under that mount — one grounding
file written as YAML takes the whole model to zero cubes.

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
- Deterministic post-processing — period deltas, ratios, share of total — over rows that
  already passed the gate, so the model stops doing arithmetic in prose.
- Pin a result as a saved report; 👍 on an answer writes a `certified_queries/` file.
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
