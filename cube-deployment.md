# The model store, and Cube as a client of it

> **Superseded.** This planned the model store that used to live in `services/semantic/`. That
> store is gone: the semantic layer is now an external deployment built elsewhere. See
> `docs/adr/0004` for what replaced it and what the portal gave up. Kept for the reasoning.

Untracked on purpose — a working document, not part of the package.

## Where the build stands (2026-08-20)

Phases 1–4 done; phase 5 steps 1–3 done. **560 tests pass, lint, layering and `tsc` clean.**

| landed | |
|---|---|
| `semantic/` | the deployable unit: `docker-compose.yml`, `cube.js`, `README.md`, `store/` (19 tests). `make store-image` / `store-test` / `store-check` |
| `semantic/tests/store_check.sh` | 11 checks against a real Cube in production mode, driven through the store's proxy — concurrent refs, the second write, fail-soft |
| `core/deployment.py` | the row: two addresses. `source` table gone, `connect` step gone, project `docker-compose.yml` gone |
| `sources/evidence.py` | the rail read off model files (17 tests) |
| `modelstore/` | the portal's client, tier 1, `files()` cached against `version` (11 tests) |
| `sources/definitions.py` | `Definition`, `parse`, `read` — moved out of `editing/edits.py`, see below |
| `web/state.py`, `web/sources.py`, `modelgen/steps.py` | every reader of the model goes through `modelstore.model_files` |
| `modelgen/publish.py` | `install` is the candidate flow. `write()` and `docker.restart()` gone from it |
| `src/web/` | four stages, no lifecycle verbs, `adopted` and `store_error` banners, `note` on the rail |

**`modelstore.model_files(ref)` is the one rule for reading the model.** No store configured
and no such ref both answer `{}`; a store that *is* configured and does not answer raises.
Five callers needed that rule and it is exactly the kind that drifts. Two places deliberately
soften it further — `web/state.view` and `web/sources.source_model` catch the outage too,
because the data page is where you find out something is down, and it ships `store_error` so
the empty rail beside it is explained rather than a lie.

**`publish.install` is the payoff, and it is verified live.** `delete candidate → create from
ref → replace → check at ref=candidate → merge or leave it`. Against chinook: 11 cubes built
and merged, then a cube with a bad `join_path` installed — `main`'s sha did not move, it kept
serving 11 entities and answered `album.count = 347`, and the rejected model sat readable at
`candidate` while that ref refused to compile. One Cube, two refs, production mode, no restart.
`Validation.candidate` carries the ref so `steps._rejected` can name it.

**A circular import had to be broken to get there** and it is worth knowing why: `modelgen/
__init__` eagerly imports `steps`, `steps` imports `evidence`, `evidence` imported `Definition`
from `editing/edits.py`, and `edits` imports `modelgen.decide`. The fix is `sources/
definitions.py` — reading the model is not an editing concern, and `modelgen`'s own docstring
already claimed nothing in it depends on `editing`. It failed only at `import
analyst_agent.web.server`, so **the whole suite passed while `analyst` would not start**.

## The portal runs nothing (decided 2026-08-20)

**`sources/docker.py` is deleted.** The portal does not start Cube — anywhere, ever. It holds
the credentials, mints the secrets, renders an `.env`, and is told an address. That settles the
open question above in favour of neither branch: there is no local container *and* no local
spinning, because "local" was never a different case.

| | |
|---|---|
| `sources/deployenv.py` | renders the `.env` for `semantic/docker-compose.yml`. One place decides what a deployment needs (7 tests) |
| `GET /api/source/env` | **the one route that returns credential values.** Handing them over is its whole purpose — nothing can start Cube without them |
| `GET /api/source/compose` | the compose file, verbatim. Served, never generated: a generated copy is a second deployment that can drift from the tested one |
| `POST /api/source` | step one — credentials in, secrets minted, **nothing running**, both addresses empty |
| `PATCH /api/source` | step two — `url` and `store_url` arrive. The same call is how a deployment that moved is followed, so there is one path rather than two |
| `src/web/src/components/Deploy.tsx` | the hand-over panel: the file, the command, and the box the address comes back in |

`deployment.deployed` is the two addresses being set, and it replaces `running` /
`container_state` everywhere. It is deliberately **not** a health state: "nothing has been run
yet" is the normal condition between the two steps, so it is neutral rather than red.

**The catalog probe was the hidden cost, and it came out better.** `read_catalog` used to run a
throwaway `analyst-probe` container with a bind-mounted probe model, and read tables from
`/playground/db-schema` — **which only dev mode serves.** It now commits the probe cubes to a
`probe` ref, asks the connected Cube about that ref, and deletes it. Tables and columns come
from `information_schema.columns` (`read.COLUMNS_SQL`, every driver, not per-dialect), so
nothing needs dev mode and nothing needs docker. Verified live against a production-mode Cube
the portal does not own: 11 tables, columns, primary keys and foreign keys, probe ref cleaned up.

`web/system.py` reports docker for the **sandbox only**, and never fatally: a machine with no
docker can connect, build a model and answer questions — it just cannot run Python.
`container.installed`/`daemon` moved to `sandbox/`, which is now the only thing here that
knows what docker is.

Remaining, in order:

4. `editing/workspace.py` — `write_text`/`unlink` → `store.edit(ref, write=…, delete=[…])`,
   keeping `edits.check` in front. This is where the second source of truth still is:
   `workspace.files()` reads the local directory, which nothing writes any more. Not
   mechanical — a bare repo has no dirty state, so `commit`/`discard`/`_dirty` and
   `vcs.status` have no counterpart and the editing UI changes shape with it. **The model
   editor is non-functional until this is done**, and was already so the moment Cube stopped
   reading the mount; `_reloaded` replaced the restart so it fails honestly rather than
   calling a container that is not there
5. delete `sources/vcs.py`, `registry.MODEL_ROOT`/`model_dir`/`scaffold_dirs` and the local
   model directory. Then rename `sources/` (deferred on purpose — `deployment/` is
   unavailable, `core/deployment.py` must stay tier 0 for `cube/client.py`)

## The sandbox is the fourth service (decided 2026-08-20)

Same shape as the model store, because it is the same question: a service someone else runs, an
address on the row, a token, a tier-1 client. **The portal now runs no containers at all.**

| | |
|---|---|
| `semantic/sandbox/` | the service: workspace per chat, scripts over HTTP, one token (19 tests) |
| `sandbox/client.py` | the portal's client — tier 1, beside `cube/` and `modelstore/` (8 tests) |
| `sandbox/profile.py` | the `Profile` shape and what it says in a prompt (6 tests) |
| row | `sandbox_url`, `sandbox_token` — **optional**, and not part of `deployed` |
| `.env` | `SANDBOX_TOKEN`, `SANDBOX_PORT` |
| deleted | `sandbox/container.py`, `scratch.py`, `image/`, `scripts/sandbox_check.py`, `make sandbox-check` |

**`--network none` is the one thing given up, and it was deliberate.** A service that answers HTTP
has a port. What survives is the guarantee that matters: the sandbox holds no credential, cannot
sign a Cube JWT and has no warehouse password, so there is still no route through it to a number
that bypassed the gate. What is lost is exfiltration — a script can now reach the network and
could send data it was already handed. `internal: true` networks would prevent exactly this but
cannot have published ports; blocking egress inside the container needs `NET_ADMIN`.

**Three traps went with the container**, and they were the expensive ones: the pid file (the image
has no `ps`), `kill -TERM` through `bash -c`, and the `OCI runtime exec failed`-on-stdout
stale-mount retry that could take out every other chat's running script. The service holds the
process object, so a stop is `os.killpg` on a session it started. It also does not use the
`timeout` binary — `asyncio.wait_for` instead, which is why its suite runs on macOS at all.

## Cube is behind the store (decided 2026-08-20)

**One address for the semantic layer.** Cube's port is not published; the store proxies it at
`/cube/{path}` and mints Cube's JWT itself from the ref in `X-Model-Ref`. So the workspace holds
the store's write token and nothing else, `CUBEJS_API_SECRET` never leaves the compose file, and
`CUBEJS_DEV_MODE=true` disabling JWT verification is no longer reachable from the host.

| | |
|---|---|
| `semantic/store/model_store/cube.py` | `mint(ref)` and `forward(...)` — a pure pass-through, path verbatim |
| `POST\|GET /cube/{path:path}` | **write token only** (12 new tests) |
| `cube/client.py` | base URL is `store_url + /cube + /cubejs-api`; `mint_token`/`LOCAL_CLAIMS` deleted |
| row | `url` and `api_secret`-as-an-address gone. `deployed` is `store_url` alone |
| `.env` | no `CUBE_PORT`; `CUBEJS_API_SECRET` documented as internal |

**The read token cannot query.** It is the one handed to Cube itself, so letting it query back
through the proxy would make the credential given away as capable as the one kept. Read-level
query scoping gets its own token later; for now it is the write token or nothing.

Two costs, taken knowingly: **no Playground and no direct `curl`** at Cube (debug via the store
or `docker compose exec`), and **the store has two jobs now** — the data model and the query
gateway. Defensible because it is the only thing that knows what a ref is, and the ref is what
selects the model Cube compiles.

### Verified live through the proxy

| | |
|---|---|
| Cube on its own port | **not published** — `expose: 4000` only |
| `/cube/readyz` no token | **401** |
| `/cube/readyz` read token | **401** — refused, as designed |
| `/cube/readyz` write token | `{"health":"HEALTH"}` |
| catalog through the proxy | 11 entities |
| a real join | Iron Maiden 21, Led Zeppelin 14, Deep Purple 11 |
| a ref that does not exist | refused, not silently served as `main` |

## The sandbox is its own deployment (decided 2026-08-20)

Moved from `semantic/sandbox/` to **`sandbox-service/`** with its own compose file, own volume
and own `.env`. It shares no network, secret or volume with the semantic layer, because it is
optional in a way the others are not: without it there are no ratios, period deltas or charts
drawn from a script, and everything else works. `GET /api/source/env` returns both files and the
panel shows the second as an explicitly optional step.

### Verified live against the running stack

Real container, real pandas and duckdb, chinook behind it:

1. `/health` needs no token; `POST /run` without one is **401**
2. a script printing a dataframe sum — output arrived **as a stream**, then one result line
3. `chart.png` reported in `wrote`, keyed on (size, mtime) rather than names
4. an upload profiled **on arrival**: `2 rows × 2 columns`, per-column distinct/min/max
5. duckdb over that upload from a second script — `sum(revenue) = 225`
6. `forget` dropped the workspace; the listing went empty
7. **the kill path**: a script that spawned a child, stopped mid-run — dead in 1.5s with code
   −15, no orphan left (`/proc` shows only `docker-init` and the service), service still up,
   next script normal

### Verified live, end to end

Fresh `DATA_DIR`, fresh store volume, chinook on `:5433`, production mode, JWT on:

1. `POST /api/source` → `deployed: false`, both addresses empty, health "not deployed"
2. `GET /api/source/env` → the file, written to `semantic/.env` unmodified but for
   `CUBESTORE_TAG=arm64v8` and the two ports, exactly as its own comments instruct
3. `docker compose up -d` → store answers `{"ok":true,"refs":1}`, Cube `/readyz` 200
4. `PATCH /api/source` with both addresses → `deployed: true`, healthy, `serving_state: empty`
5. `read` → 11 tables with keys and foreign keys, **no container**, probe ref gone afterwards
6. `cubes` → 11 cubes via the candidate ref, merged to `main`, candidate deleted
7. `album.count` by `artist.name` → Iron Maiden 21, Led Zeppelin 14, Deep Purple 11

Note `semantic/.env` is gitignored by the bare `.env` pattern, which matches at any depth —
worth knowing, since that file holds the warehouse password.

## Three components, and no modes

```
   ┌──────────┐  read/write   ┌─────────────────┐   pull    ┌───────────┐
   │  portal  │ ────────────▶ │   model store   │ ◀──────── │   Cube    │
   │ analyst  │ ◀──────────── │  git + HTTP API │           │  (stock)  │
   └──────────┘               └─────────────────┘           └───────────┘
         └──────────────────── query ──────────────────────────────▶
```

- **The model store owns the data model** — the files, the branches, the history. One owner.
- **Cube is the stock `cubejs/cube` image** with a mounted `cube.js` that pulls from the store. No fork.
- **The portal is a client** of both: it reads and writes the model over HTTP, and queries Cube over HTTP.

The store and Cube are deployed together — one compose file, two containers, whether that is this laptop
or a datacenter. **The portal's code is identical either way**, which is why there is no `local` versus
`remote` mode, no `capabilities` branching on one, and no second delivery path. There are two URLs.

## Why this shape

Every earlier draft put our code somewhere it did not belong — inside Cube's image, or inside Cube's
volume — because the model had no home of its own. Giving it one deletes all of that: the image fork and
its release treadmill, multi-arch builds, `supervisord`, two processes in one container, the health-check
lie, the trademark question, writing into Cube's filesystem, and the inotify directory-rename trap.

It also puts the reachability question to rest. The store sits **beside Cube**, so Cube reaches it over the
compose network and the portal reaches it the same way it already reaches Cube. Nothing has to reach back
into the portal on `127.0.0.1`.

**Naming.** It is the *store*, not the *model*: `modelgen` is already named that way because "model" in
this codebase means the LLM, and `registry.py` already means the source registry. The portal-side client is
`modelstore/`.

## What the store is

Small, because **git is the storage engine** — no database, no migrations, no schema to evolve.

```
GET    /health
GET    /refs                    -> branches with head shas
POST   /refs                    { name, from }
DELETE /refs/{name}
GET    /files?ref=              -> { path: content }          <- Cube and the portal
GET    /files/{path}?ref=       -> one file
PUT    /files?ref=              { files, message } -> { sha }  <- whole-set replace, one commit
PATCH  /files?ref=              { write, delete, message } -> { sha }
GET    /version?ref=            -> { sha }                     <- Cube's schemaVersion
GET    /diff?base=&ref=         -> changed paths and a unified diff
POST   /merge                   { from, into, message } -> { sha } | conflict
GET    /log?ref=&limit=
```

**Two tokens, not one.** A read token for Cube, a write token for the portal. Cube has no business writing,
and the read token sits in a container we may not control.

**The store holds policy about files, not policy about models.** It enforces a path allowlist
(`cubes/**.yml`, `views/**.yml`) and valid YAML, and it **refuses a `*.yml` anywhere but
`cubes/` and `views/`** — because a stray `*.yml` elsewhere takes a whole model to zero cubes, and that
guard should protect every client rather than living in one. What counts as a *valid edit* — cubes
append-only, views freely rewritable — stays in the portal's `edits.py`, because that is the portal's
opinion, not a property of storage.

**`vcs.py` moves into the store** rather than being reimplemented. It is already git-as-subprocess against
a model directory; it gains an HTTP surface and leaves the portal.

**The store ships as an image**, built from `model-store/` in this repo with its own Dockerfile. `uv_build`
only ships `src/analyst_agent`, so it does not reach the wheel.

## Cube's config, and the one detail that must not be got wrong

```js
const REF = ({ securityContext }) => securityContext?.ref || process.env.MODEL_REF || 'main';

module.exports = {
  // One compiled model per ref. Without this, Cube compiles once and the ref is ignored.
  contextToAppId: (ctx) => `cube_${REF(ctx)}`,
  schemaVersion:  (ctx) => cachedVersion(REF(ctx)),
  repositoryFactory: (ctx) => {
    const ref = REF(ctx);                    // safe to capture: ref is part of appId
    return { dataSchemaFiles: () => fetchFiles(ref) };
  },
};
```

⚠️ **`schemaVersion` is called on every request, so `cachedVersion` must fail soft** — short TTL, and on
error return the last known value. The naive implementation takes Cube down with the store, and since the
store is now in the query path that is the single most consequential line in this design.

Two traps behind it, both verified in Cube's docs:

- **`repositoryFactory` is called once per app id; the `dataSchemaFiles` it returns runs on every
  recompile.** Fetch inside the method, never in the factory body, or the model updates once and never
  again. Test the *second* write, not the first.
- **Anything the factory captures must be part of `contextToAppId`.** It keeps whichever
  `securityContext` it saw first — there are upstream bugs for exactly this. `ref` is safe because the app
  id derives from it.

**Who asks for which ref**, decided once so nothing infers it:

| caller | ref |
|---|---|
| question chats, dashboards, MCP | `main`, pinned — a board must never silently read a branch |
| model chats | the chat's branch |
| candidate validation | the candidate ref |

`LOCAL_CLAIMS` grows a `ref`. This makes [db.py:16](src/analyst_agent/core/db.py#L16)'s note that
`chats.branch` is *"provenance, not a binding — one container means one checkout"* false in the good
direction: a model chat now genuinely queries its own branch.

## Generation stops being destructive

The best thing this buys, and it fixes a known pain point: **a failed build never touches `main`.**

1. the portal generates the file set
2. `PUT /files?ref=candidate` — one commit on a throwaway ref
3. query Cube with a token carrying `ref=candidate` → `wait_until_compiled` → `check()`
4. compiles → `POST /merge {from: candidate, into: main}`; does not → delete the ref

Today `publish.write` unlinks every `*.yml` and a rejected model is left on disk, recoverable only by
`git diff` in a directory the user happens to be versioning. Now a rebuild is atomic, reviewable and
abandonable. This also answers the tier-3 ordering problem — validating no longer means deploying over
whatever `main` is serving, because refs are served side by side.

`compilerCacheSize` defaults to 250 compiled models with LRU eviction, so simultaneous refs are not a
practical constraint. Pre-aggregations are: `scheduledRefreshContexts` must name each context, so build
them for `main` only and let candidates hit raw SQL.

## The deployment row

```
deployment(id=1, label, url, api_secret,
           store_url, store_token,
           credentials, created_at, updated_at)
```

- `url` / `api_secret` — where queries go and what signs the JWT.
- `store_url` / `store_token` — the model store. **Separate from `url`**, because they are two ports on the
  same host and conflating them is how a write ends up POSTing at the query API.
- `credentials` — **only** for the introspection probe, and so only needed to generate a model from
  scratch. Cube has its own. Adopt-and-edit needs none.
- No `mode`. No `git_remote` — see backup, below.

**A table, not columns on `source`.** `CREATE TABLE IF NOT EXISTS` adds a table to a live `analyst.db` and
*silently* does not add a column, so this ships with no rebuild. `source` is dropped from `SCHEMA` and left
orphaned in existing files — cheaper than a migration nobody else will run.

**The address stops living in `.env`.** `settings.cube_url`, `api_root`, `cube_port` and `cubejs_dev_mode`
are deleted so nothing can read a stale second copy. CLAUDE.md's "there is one address, and it is
`CUBE_URL`" becomes "there are two, and they are on the deployment row."

## Running it

**Locally**, the portal starts both containers with two `docker run`s built from the row, on a shared
network — the same way it starts one today. **No generated compose file**, which keeps CLAUDE.md's rule
that the container is created from the database and there is nothing on disk to drift from.

**Remotely**, the user gets a compose snippet to paste. That is documentation, not a generated artefact.

The project's `docker-compose.yml` is **deleted**: since Cube v0.26.48 the single image runs Cube Store
*inside* the Cube process when `CUBEJS_DEV_MODE=true`, with pre-aggregations as files under `.cubestore`.
So the local flow needs no router and no worker.

⚠️ **Production mode does need Cube Store, and it is not optional** — measured, not assumed: with
`CUBEJS_DEV_MODE=false` and no `CUBEJS_CUBESTORE_HOST`, `/v1/meta` compiles and serves normally while
every `/v1/load` fails with *"Cube Store was specified as queue/cache driver."* Metadata working while
queries do not is a confusing failure, so it is worth knowing which half breaks.

That raises the price of production mode from "one flag" to "a third service", and it is why
`deploy/docker-compose.yml` ships three: Cube, Cube Store, and the model store. Anything reachable from
outside its host needs all three, because the alternative — dev mode — serves the warehouse
unauthenticated.

**No lifecycle verbs** — no start, stop, restart or pause. A credential change means recreate, because a
container's environment cannot be edited; that is the only reason `create` exists.

**Dev mode versus production mode is decided by exposure, not preference.** `CUBEJS_DEV_MODE=true` disables
JWT verification, so anything on a public address must run production mode. Note that the file watcher is
irrelevant here either way — nothing writes into Cube's filesystem now, and `schemaVersion` is what triggers
a recompile in both modes. **That is a real simplification: dev mode stops being load-bearing.**

**The introspection probe is the one exception** — a throwaway plain-image container with a bind-mounted
`probe-model`. It is not a deployment and it is created, read and destroyed in one operation, so routing it
through the store would add latency and a failure mode for nothing. `modelgen/read.py` is untouched.

## The rail: `read → cubes → describe → views`, from evidence

`steps` records what this portal ran; the model files record what exists. For an adopted store only the
second is populated, and a rail keyed on the first reports a working model as unstarted.

`sources/evidence.py` — `of(definitions, state, served=N) -> Evidence`, where `definitions` now come from
`GET /files?ref=main`:

| stage | what proves it ran |
|---|---|
| `read` | a catalog cached in `state` — it writes no files, so that *is* its artefact |
| `cubes` | `cubes/*.yml` exist |
| `describe` | cube bodies carrying a `description`, reported as a **count** |
| `views` | `views/*.yml` exist |
| `adopted` | nothing in the store, and Cube is serving cubes anyway |

Rules that are not obvious:

- **A failed run wins over the artefacts it left behind.** `steps` is the newer information, and a
  half-written model must not hide the error.
- **`invalidated_by` reads evidence, not `steps`.** For an adopted store `steps` is empty while the views a
  rebuild would delete are right there.
- **Blocking is on the state a step reads, not on a tick.** `describe` reads `state["bodies"]`, so an
  adopted model with cubes and no bodies is honestly `blocked → "cubes"`.
- `describe` is a count because nobody re-describes a whole model to finish the last two.

**Placement.** `evidence` needs `Definition` from `editing/edits.py`, so `store → evidence → editing →
modelgen → steps → store` is a cycle. `sources/store.py` becomes **storage only**; `stages`,
`invalidated_by` and `public` move into `evidence.py`. `tests/test_evidence.py` (untracked, from a lost
session) is a good spec.

## `connect` stops being a step

A step records that something *once* succeeded; a URL that stopped answering makes such a record a lie.
`connect` is deleted, the rail is four stages, and what replaces it is `health.check(deployment)` over both
URLs plus a create-if-missing on the two containers.

## Onboarding is the gate

No deployment → the portal shows onboarding, not an empty chat. It replaces the whole shell the way the
missing-frontend 503 does; it is **not** a `Rail.Mode`. `web/state.py` answers `setup_required`.

**Run both here** — a port for Cube, a port for the store, database credentials. The portal creates two
containers and waits.

**Connect to existing** — Cube's URL and API secret, the store's URL and token. Verified by two real round
trips: an authenticated `/v1/meta`, and `GET /version`. A store already holding a model is the adopt path.
Credentials optional, labelled as only being for reading your schema.

An unreachable deployment still starts the portal: the health dot says so, chat refuses with a reason,
dashboards show the banner they already show.

## Change map

| file | change |
|---|---|
| `model-store/` *(new)* | the service: `vcs.py` moved in, an HTTP surface, a Dockerfile |
| `modelstore/` *(new, tier 1)* | the portal's client: refs, files, version, diff, merge |
| `core/db.py` | + `deployment` table; − `source` |
| `core/deployment.py` *(new)* | the row and the dataclass. Tier 0 |
| `core/config.py` | − `cube_url`, `api_root`, `cube_port`, `cubejs_dev_mode` |
| `cube/client.py` | `local()` → `for_deployment(row)`; claims grow a `ref` |
| `sources/health.py` | takes the row; checks both URLs |
| `sources/docker.py` | two `docker run`s; − lifecycle verbs, − Cube Store, − model mount |
| `sources/vcs.py` | **moved to the store** |
| `sources/registry.py` | − `MODEL_ROOT`, `model_dir`, `scaffold_dirs` |
| `sources/editing/workspace.py` | `PATCH /files` instead of the filesystem |
| `sources/editing/edits.py` | main's blobs via `GET /files?ref=main` |
| `sources/evidence.py` *(new)* | the rail from the store's files |
| `sources/store.py` | folds into `core/deployment.py`; what remains is storage only |
| `modelgen/publish.py` | `write` → `PUT /files?ref=candidate`; − restart; + the merge-on-pass flow |
| `modelgen/steps.py` | − `connect`; `STAGES` is four |
| `modelgen/read.py` | untouched |
| `agent/tools/editor.py` | six tools through the client |
| `web/model_edit.py` | proxies the store; validate targets a candidate ref |
| `web/state.py` | `setup_required`; **rebuild cached clients when either URL changes** |
| `docker-compose.yml` | **deleted** |
| `Makefile` | − `up`/`down`/`logs`; + `store-image` |
| `src/web/` | `Onboarding`, deployment card, deploy state; rail is four |

Untouched: `cube/gate.py`, `catalog.py`, `shape.py`, `visualize.py`, all of `dashboards/`, all of
`sandbox/`, `transcript.py`.

## Invariants in CLAUDE.md that change

- "There is one address, and it is `CUBE_URL`" → two, on the deployment row.
- "`cube/model/` is the only thing written into the working directory" → nothing is; the model is not on
  this filesystem at all.
- "`git checkout` on the host *is* the deploy" → gone. There is no checkout; a ref is selected per query.
- "Impact is only ever measured against the branch actually checked out" → against any ref, because refs
  are served concurrently. This is the constraint that lifts.
- "The gate runs before the write, because the write is live" → the write is a commit on a ref, and only
  the ref you ask for is served. The gate still runs first; it is no longer the only thing standing
  between a bad file and a broken model.
- `publish.install` "write → restart → ask" → "commit to a candidate → ask → merge or discard".
- The docker-compose / Cube Store / `analyst-net` paragraphs go entirely.
- Four steps → `read → cubes → describe → views`.

## Sequencing

Each phase leaves the app working, and the first two touch no portal code.

1. ~~**The store, standalone.**~~ **Done.** `model-store/` — a bare repo driven by git plumbing (no
   working tree, since refs are served concurrently and a service must never hold a dirty one),
   `update-ref` compare-and-swap instead of a lock, `merge-tree --write-tree` so a conflict cannot leave
   markers in a file Cube would compile. 19 tests, `make store-test`.
2. ~~**The `cube.js` and the compose snippet.**~~ **Done.** `deploy/cube.js`, `deploy/docker-compose.yml`,
   and `semantic/tests/store_check.sh` (`make store-check`) — self-contained, standing up its own network,
   database, secret and volume. 11 checks, all passing against a real Cube in **production mode**, so the
   ref arrives on a verified token rather than through a dev-mode bypass:
   two refs served at once; a query through a cube only the branch has; **the second write** picked up
   (an eager factory passes the first and never again); a merge reaching main with no restart; and the
   store stopped while Cube keeps serving both metadata and queries.
3. **Deployment row.** `CubeClient` and health take it; `connect` deleted; `settings` pruned; `docker.py`
   starts two containers. The portal still reads its local model directory.
4. **Switch the readers** — `evidence`, `edits`, the editor tools, `web/model_edit`.
5. **Switch the writers** — `publish.install` to the candidate-ref flow, `workspace` to `PATCH /files`;
   delete the local model directory.
6. **Onboarding.**

Phases 4 and 5 are the real work. Phase 2 is the one that must not be skipped: it is where the fail-soft
behaviour is proven, and everything else assumes it.

## Caveats

**The store is in the query path.** `schemaVersion` runs per request, so a store outage must degrade to a
stale model rather than an outage. Cache with a short TTL, return the last known value on error, and prove
it in phase 2.

**The store's volume is now the model, and it is the new single point of data loss.** Today the model sits
in a git repo in the user's own working directory. In a container volume it is much easier to lose. The
backup story is the git remote that used to be "history": **the store pushes to a remote the user owns.**
Out of scope for v1, but it should be the first thing added after it, and the onboarding should say plainly
that nothing is backed up until it exists.

**Editing requires the store; querying does not.** Store down and Cube up is a working chat with a broken
model editor. That is the right way round, and worth surfacing rather than letting the editor fail opaquely.

**Three containers locally** — Cube, the store, the sandbox — plus the probe transiently. Docker is now
unambiguously a hard requirement.

**Token rotation means recreating containers**, since environment cannot be edited. Two tokens, two places.

**A local install never exercises JWT verification** if it runs dev mode, so a wrong `api_secret` first
appears against a production Cube. Onboarding must do an authenticated `/v1/meta` round trip; `/readyz` is
unauthenticated by design and passes regardless.

**Generation needs docker *and* a database reachable from this machine**, because the probe is a local
throwaway container. Point at a Cube whose warehouse is not reachable here and you can only adopt-and-edit.
Say so on the form, not at failure time.

## Still open

- **Whether the store pushes to a git remote**, and whether that is configured on the store or through the
  portal. It is the backup story, so "later" should be short.
- **Two deployments at once** — one to build against, one to query. Now cheaper than it has ever been, since
  refs are already concurrent; but a dashboard refreshed against the wrong deployment is still the same
  silent-wrong-numbers bug. One active deployment, switchable, until there is a per-dashboard binding.
- **Conflict handling on `POST /merge`.** Abort and report, never leave markers in a tree — a conflicted
  `*.yml` is "zero cubes" arrived at from a different direction.
- **Whether the store validates against Cube itself.** It could compile a ref before allowing a merge to
  `main`. Tempting, and wrong for now: it would make the store depend on Cube, and the portal already owns
  that step.
