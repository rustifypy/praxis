# Refactor: two integrations, one model-store package, no dashboards package

The rule this refactor installs, replacing the six-tier table in `CLAUDE.md`:

> **Tier 1 is external integrations, and there are exactly two: the semantic deployment and the
> sandbox.** Everything process-local — settings, the sqlite file and every table's accessors,
> events, the LLM builder, the job runner, the deployment row and its hand-over — is tier 0.

Everything below follows from that sentence. `cube/`, `modelstore/`, `datamodel/` and `drafts/`
are one integration and become one package. `dashboards/` is not an integration, not HTTP and not
one thing, so it stops being a package and its four parts go where each belongs.

## Target

| tier | package | what |
|---|---|---|
| 0 | `core/` | settings, the sqlite file **and every table's accessors**, the event format, the LLM builder, the job runner, the deployment row and its hand-over |
| 1 | `modelstore/` | the model store, Cube behind it, the model it serves and everything that generates or edits that model |
| 1 | `sandbox/` | where the agent runs Python |
| 2 | `agent/` | prompts, toolsets, one turn |
| 3 | `web/` | HTTP. May reach anything; nothing may reach it |

Four tiers instead of six. Two packages at tier 1 instead of six modules.

`core/llm.py` is also an external integration and stays at tier 0 anyway — it is a builder, not a
client, and every tier needs it. The rule is stated as it is above, with that exception named, so
that the next reader does not "fix" it.

### Why `modelstore/` and not `semantic/`

Two packages named after two services, rather than a concept sitting beside a service. And it puts
the thesis in the file tree: the store is the only door to Cube, so the door's name is on the
package. `CONTEXT.md` already says as much — **Store** is "owner of the data model's files and
their history, and the only door to Cube".

It also cancels most of the renaming. `ModelStore`, `StoreError`, `StoreError.missing`,
`for_deployment` and `open_store` all keep the names they have today, so the client merge is
`cube/client.py` disappearing into a class that already exists rather than a third name arriving.

Three consequences, to be accepted knowingly rather than discovered:

1. **"semantic" survives on the wire.** `RENDERERS = {"semantic": …, "sandbox": …}` in
   `web/connection.py`, `deployenv.semantic`, `reachable.semantic` and
   `GET /api/deployment/semantic/compose` are the service key the browser posts. None of them
   changes — `tests/surface.json` stays byte-identical. So the package is `modelstore` and the
   thing it integrates with is addressed as `semantic`.
2. **`services/semantic/` keeps its name.** It is the compose deployment: three containers,
   `model-store`, `cube`, `cubestore`. One of them is the model store; the directory is not. Same
   split as (1), and fine — but it means `modelstore` denotes the package and the container, while
   `semantic` denotes the deployment and the wire key.
3. **`modelstore` grows from a 234-line client to a ~3,600-line package.** The word stops meaning
   "the store's HTTP client" and starts meaning "everything about the data model". That is a
   glossary edit, not a comment — see Step 6.

---

## The destination map

### `modelstore/` — absorbs `cube/`, `datamodel/`, `drafts.py` and `dashboards/tiles.py`

```
modelstore/
  client.py       ← cube/client.py + modelstore/client.py       MERGED (see below)
  catalog.py      ← cube/catalog.py                             unchanged
  schema.py       ← cube/schema.py                              unchanged
  gate.py         ← cube/gate.py                                signature change only
  shape.py        ← cube/shape.py                               unchanged
  visualize.py    ← cube/visualize.py                           unchanged
  charts.py       ← dashboards/tiles.py                         renamed, unchanged
  drafts.py       ← drafts.py                                   unchanged
  definitions.py  ← datamodel/definitions.py                    unchanged
  evidence.py     ← datamodel/evidence.py                       unchanged
  registry.py     ← datamodel/registry.py                       unchanged
  serving.py      ← datamodel/serving.py                        unchanged
  reachable.py    ← reachable.semantic + reachable.querying     split from reachable.py
  editing/        ← datamodel/editing/                          impact.py signature change
  modelgen/       ← datamodel/modelgen/                          unchanged
```

`dashboards/tiles.py` becomes `modelstore/charts.py`: it imports only `gate`, `catalog`, `schema`,
`shape` and `visualize` — never a dashboard row. `CHART_TYPES` belongs beside the renderer's
`MAX_BARS`/`MAX_SLICES` limits, which is the pairing `CLAUDE.md` already asks for ("widen the
renderer branch *before* the tuple"). Renamed because `tiles.py` inside `modelstore/` would read as
dashboard code; `charts.py` says what it is — chart rules over a query's columns.

`drafts.py` moves in whole. A draft is a working copy of a **Ref**, which is a semantic-layer
noun; nothing outside this package will import it after the move.

### `sandbox/` — unchanged, plus its half of reachability

```
sandbox/
  client.py       unchanged
  profile.py      unchanged
  reachable.py    ← reachable.sandbox + reachable.alive
```

### `core/` — gains the row accessors and the hand-over

```
core/
  config.py       unchanged
  db.py           unchanged — already the schema for every table
  deployment.py   unchanged
  deployenv.py    ← datamodel/deployenv.py
  transcript.py   ← transcript.py
  boards.py       ← dashboards/store.py + dashboards/layout.py
  events.py, jobs.py, llm.py, access.py    unchanged
```

`deployenv.py` moves to `core/` and **not** into `modelstore/`. It renders the compose hand-over for
*both* services — it holds the `SANDBOX` template and picks `CUBESTORE_TAG` off
`platform.machine()`. Inside `modelstore/`, the sandbox's deployment file would be generated from
inside the model store's package. It belongs beside the deployment row it decorates.

`boards.py` is `dashboards/store.py` (376 lines) with `dashboards/layout.py` (77) appended.
`layout.py` imports nothing but `Tile`; it is geometry over rows and has no other home. The
precedent for both is already in the tree: `transcript.py` is 157 lines of typed accessors over
`chats`/`events`/`results` and never earned a package. `dashboards/store.py` got a tier because it
was bigger, which is not a reason.

`core/` goes from ~740 to ~1,590 lines. See **What this costs** below.

### `web/dashboards/` — the page's half

```
web/dashboards/
  routes.py       ← web/dashboards.py routes
  controller.py   ← web/dashboards.py logic + dashboards/refresh.py
  dto.py          ← web/dashboards.py request/response models
  delta.py        ← dashboards/delta.py
```

`refresh.py` and `delta.py` are imported by nothing below tier 5 today (verified). `CLAUDE.md`
already says "the agent authors and arranges; **the page runs**" — this is that sentence in the
file tree. It also finishes the noun-package conversion `chat/`, `pipeline/`, `result/`,
`workspace/` and `agentaccess/` already went through.

`web/model.py` and `web/connection.py` stay flat modules. Converting them is a separate change and
is not in this plan.

### Deleted

- `cube/` — every file moved; `cube/client.py` merged into `modelstore/client.py`
- `datamodel/` — every file moved
- `dashboards/` — split four ways
- `reachable.py` — split two ways
- `drafts.py`, `transcript.py` — moved

---

## The one real design decision: the merged client

Two `httpx.AsyncClient`s point at the same `base_url` with the same bearer token today, with two
`_send`s, two error types, and two `for_deployment` / `open_*` pairs. `agent/turn.py` and
`web/model.py` each open both against one address.

### Ref binding

`CubeClient` is **ref-bound** — `self._ref` is baked into every path and the catalog is cached per
instance. `ModelStore` is **ref-parameterised** — every method takes `ref=MAIN`. The merge must
pick one, and `workspace.stage` decides it: that function reads **main and the edited ref in the
same call** (`store.files(MAIN)` while staging onto ref X). A purely ref-bound merged client needs
two instances there.

So: **ref-parameterised transport, plus a bound view.**

```python
class ModelStore:
    """One address: the model store, and Cube behind it."""

    # model side — raises StoreError on 4xx
    async def refs(self) -> list[Ref]
    async def create_ref(self, name, from_ref=MAIN) -> Ref
    async def delete_ref(self, name) -> None
    async def rev(self, ref=MAIN) -> int
    async def rebase(self, ref) -> int
    async def files(self, ref=MAIN, *, refresh=False) -> dict[str, str]
    async def file(self, path, ref=MAIN) -> str
    async def diff(self, ref, base=MAIN) -> dict
    async def edit(self, ref, write=None, delete=None, message=...) -> int
    async def replace(self, ref, files, message) -> int
    async def merge(self, from_ref, into=MAIN) -> int

    # query side — a Cube rejection is data, never an exception
    async def compile(self, query, ref=MAIN) -> CompileResult
    async def load(self, query, ref=MAIN) -> LoadResult
    async def catalog(self, ref=MAIN, *, refresh=False) -> Catalog
    async def wait_until_compiled(self, ref=MAIN, ...) -> Catalog

    def at(self, ref) -> Bound     # the ref-bound facade
```

`Bound` is a thin view holding `(client, ref)` and forwarding `compile`/`load`/`catalog`/`files`
with the ref filled in. `gate.check`, `gate.execute`, `gate.run`, `refresh`, `publish.install` and
`agent.session` all take a `Bound` and their call sites do not change shape.

### Two behaviours that must survive verbatim

1. **The query side never raises on a Cube rejection status.** `cube/client.py::_send` returns
   `(status, body, text)` and lets `compile()` return `ok=False`; the store's `_send` raises on
   4xx. The merged client keeps both — model methods raise, query methods return. This is the
   likeliest casualty of a careless merge, and it is the whole reason the gate can hand Cube's
   rejection to the agent as text.
   `load()` keeps raising on a Cube error, because `gate.execute` catches it and turns it into
   `GateResult(ok=False)`. Do not "fix" that asymmetry.
2. **`StoreError.missing`.** Per `docs/adr/0002`, it has three production callers
   (`modelgen/read.py`, `modelgen/publish.py`, `model_files`). It becomes
   `StoreError.missing` and keeps working — same class, same name, no call-site change.

### Errors

One `StoreError(RuntimeError)` — the existing class — with `.status`, a `.missing` property, and a `.where` field of
`"store" | "cube"`. `workspace.verify` distinguishes "the store refused" from "Cube did not
answer" in its message today via two `except` clauses; after the merge that becomes one clause
reading `.where`. The distinction survives as a field, not as two classes.

`CubeError.unreachable` and `SandboxError.unconfigured` already have zero production callers
(ADR 0002 counted them). Nothing new is needed.

### Caches

Both survive, both keyed by ref on the one object:

- files: revalidate-by-rev past a 5s TTL (`FILES_TTL_S`)
- catalog: 30s TTL (`CATALOG_TTL_S`)

### Timeouts

`ADMIN_TIMEOUT_S`, `POLL_*` and `settings.query_timeout_s` stay under the store's proxy cap;
`RUN_TIMEOUT_S` / `UPLOAD_TIMEOUT_S` in the sandbox stay above the service's own. Both directions
are asserted by `tests/test_timeouts.py` — it must keep passing unchanged in meaning.

### Constructors

`ModelStore.for_deployment(deployment)` and `open_store()` — both of which already exist — absorb
`CubeClient.for_deployment`, `open_client`, `served_catalog` and `model_files`.
`served_catalog(ref=...)` and `model_files(ref=...)` are convenience wrappers used in six places
between them; keep both as module functions on the new client module so those call sites stay one
line.

---

## The two edges that invert

### 1. `impact.check` takes the saved queries

`datamodel/editing/impact.py` reads `dashboards.store` today to answer "what would this edit
break". After the move that would be `modelstore/` importing `core.boards`, which is legal but
wrong: the model store's package should not know dashboards exist.

```python
# before
async def check(catalog: Catalog) -> Impact

# after
def check(catalog: Catalog, saved: list[SavedQuery]) -> Impact
```

where `SavedQuery` is `(kind, where, title, query)` — a small dataclass in `modelstore/editing/`,
constructed by the caller. Both callers (`web/model.py` and `workspace.verify` via its caller) have
the rows to hand. This is `CLAUDE.md`'s own rule: "What a lower tier can't look up, it takes as an
argument."

Note this makes `check` synchronous, which removes the last reason `impact` is async.

`workspace.verify` currently calls `impact.check` itself, so `verify` grows a `saved` parameter
that its two callers pass through. That is deliberate — `verify` is the one place that knows
whether a compile succeeded, and threading the list is cheaper than giving `modelstore/` a reader.

### 2. `gate` and `refresh` take a `Bound`, not a `CubeClient`

Mechanical, but it touches `agent/tools/query.py`, `web/agentaccess/tools.py`,
`web/support/state.py`, `web/dashboards/controller.py` and `modelstore/charts.py`.

---

## Execution order

Each step ends green on `make lint && make test`. Do not batch them.

### Step 0 — rewrite the spec first

Rewrite `TIERS` in `tests/test_layering.py` to the four-tier table above. Every subsequent step is
"make this test pass". **This is the entire safety net for a ~3,600-line relocation** — the
deterministic suite covers nothing container- or LLM-dependent, so nothing else will tell you a
move was wrong.

Add one assertion while you are there: `modelstore` must not name `boards`. It is the edge this
refactor exists to cut and it will be re-added by accident.

Expected state: red, ~8 failures. Commit the test alone.

### Step 1 — merge the two clients

`modelstore/` stays where it is and grows. Fold `cube/client.py` into `modelstore/client.py`, then
move `cube/{catalog,schema,gate,shape,visualize}.py` across so the client has its `Catalog` to
build. Delete `cube/`.

Update the 24 import sites. Run `tests/test_client.py`, `test_modelstore.py`, `test_gate.py`,
`test_timeouts.py`, `test_catalog.py`, `test_schema.py`, `test_shape.py`, `test_visualize.py`.

Expected: `test_client.py` and `test_modelstore.py` need real edits (they construct the two
classes); the rest need import lines only.

### Step 2 — `datamodel/` and `drafts.py` into `modelstore/`

Straight move of `definitions`, `evidence`, `registry`, `serving`, `editing/`, `modelgen/`,
`drafts.py`. `deployenv.py` goes to `core/` in the same step — it is the only file in `datamodel/`
that does not follow the rest, and leaving it behind for a later step means `datamodel/` lingers
as a one-file package.

Expected: import lines only, plus `web/connection.py` changing `from ..datamodel import
deployenv` to `from ..core import deployenv`.

### Step 3 — split `reachable.py`

`semantic()` + `querying()` → `modelstore/reachable.py`, keeping both function names. `sandbox()` + `alive()` →
`sandbox/reachable.py`. `test()` — the wizard's two-way dispatch — goes to `web/connection.py`,
which is its only caller and the only place that has both.

`Reached`, `_failed`, `_rejected`, `_mismatch`, `_body` are shared by both halves. Duplicate them
rather than making a third module: `docs/adr/0002` already accepted this exact duplication for the
clients, and a shared `reachability.py` at tier 1 would be a third package at a tier this refactor
just defined as holding two.

`docs/adr/0001` is untouched by this: the two questions it defends (`semantic` vs `querying`) both
land in `modelstore/reachable.py`, still separate, still for the reasons the ADR gives.

### Step 4 — dissolve `dashboards/`

In one commit, because the package cannot half-exist:

- `store.py` + `layout.py` → `core/boards.py`
- `tiles.py` → `modelstore/charts.py`
- `refresh.py` → `web/dashboards/controller.py`
- `delta.py` → `web/dashboards/delta.py`
- `web/dashboards.py` → `web/dashboards/{routes,controller,dto}.py`

`agent/tools/dashboards.py` re-points at `core.boards` and `modelstore.charts` — 24 call sites, all
mechanical, no signature changes. The agent keeps authoring dashboards, which is the whole reason
the rows go to `core` and not to `web`.

Rename the test files with the code: `test_dashboard_store.py` → `test_boards.py`,
`test_tiles.py` → `test_charts.py`, `test_dashboard_refresh.py` stays.

### Step 5 — invert `impact.check`

Signature change, `SavedQuery` dataclass, `workspace.verify` grows `saved`, two call sites pass it.
`tests/test_impact.py` and `tests/test_workspace.py` need real edits.

Do this last: it is the only step with a behavioural change in it, and doing it after the moves
means a failure here is unambiguously this step's.

### Step 6 — documentation

`CLAUDE.md`: the tier table, "The subsystems" section headers, every path reference. The
"Layering — the load-bearing rule" prose is rewritten around the two-integrations rule.

`CONTEXT.md`: **Store** currently means "the model store" — one service, one client. After the
merge `modelstore` names a package holding the client, the served model, generation and editing.
The entry has to say that the store and Cube are **one client on this side** while remaining two
containers on the deployment side, and that `semantic` is what a deployment of them is called.
Without that, the glossary has one word for two sizes of thing.

`docs/adr/0002-three-clients-share-no-transport.md`: **amend, do not delete.** It refused a shared
transport base class under three clients. This refactor does something different — it merges two
clients that were always one address. Add a "Superseded in part" section saying so, or the next
reader will re-split them on the ADR's authority.

`docs/adr/0003-staging-lives-in-the-browser.md`: already stale before this refactor. It says a
portal-side copy of an unsaved edit was rejected; `drafts.py` is exactly that copy on disk. Fix it
or mark it superseded — do not leave it as the authority on where a draft lives.

`dashboards/__init__.py` is currently empty while `CLAUDE.md` says "Read `dashboards/__init__.py`
before touching this; its three invariants". Those three invariants (the row is the truth, a
refresh is atomic, a refresh re-executes) need a home: put them at the top of
`web/dashboards/controller.py`, which is where all three now live.

---

## What this costs

**`core/` roughly doubles**, ~740 → ~1,590 lines: settings, llm, events, jobs, the db, four sets of
row accessors, layout geometry and both compose templates. `test_core_names_nothing_outside_itself`
still passes, but the narrative guard changes from "core is small" to "core is process-local state
and imports nothing above it". This is the part most likely to rot, and it is being accepted
knowingly.

The fallback if it rots: a fifth package for local rows (`store/`, tier 1 beside the integrations).
It is cheaper but contradicts "tier 1 is two integrations", which is why it is not the plan.

**`modelstore/` is ~3,600 lines in one package** — an HTTP client, an LLM generation pipeline, a YAML
edit gate, chart rules and the served catalog. One package, deliberately. **Not one class**: the
client is transport, and `modelgen/`, `editing/`, `catalog.py` and `gate.py` are its neighbours,
never its methods.

**Name pressure.** `modelstore/` will contain `gate.check`, `edits.check`, `impact.check` and
`publish.check`. All four are already qualified at every call site and none is ambiguous in
context, but `CLAUDE.md`'s "a name is taken once" is now under strain and a fifth `check` should be
refused.

**Nothing container- or LLM-dependent is covered.** After Step 6, run `make store-check` and one
live model generation against a real source before trusting any of it. The suite will be green
long before the thing works.

---

## Not in this plan

- Converting `web/model.py` and `web/connection.py` to noun packages
- Any change to `services/semantic/` or `services/sandbox/`
- Any change to the HTTP surface — `tests/surface.json` must be byte-identical at the end
- Merging `sandbox/client.py` into anything
- Touching `agent/` beyond import lines and the `Bound` signature change
