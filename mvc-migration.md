# The HTTP layer becomes routes, controllers and DTOs

Planned 2026-08-24. **Not started** — Part 1 has four decisions still open, and the phases below
are ordered so each one ends with a green suite. Nothing here changes what the portal does; it
changes where the code that does it lives, and it makes the wire format something you can read.

---

## Part 0 — What this is, and what it is not

### The ask, as I read it

> "implement MVC architecture … read files in web and start moving datamodel and dto out, routes
> separate and controller separate. Give two line docstring for each route. and you can totally
> change route names and change frontend as well."

**Assumption, and the one place I could be wrong:** "moving datamodel out" means *move the
response data shapes out of the route functions* — not "move the `datamodel/` package", which is
tier 3 and has nothing to do with HTTP. `datamodel` is a taken name in this repo
(`src/analyst_agent/datamodel/`, the connection and model generation), so the word is ambiguous in
a way worth settling before any file moves. Everything below assumes the first reading.

### The M in MVC already exists

The "model" of MVC is tiers 0–4: `core`, `cube`/`modelstore`/`sandbox`/`transcript`,
`dashboards`, `datamodel`, `agent`. **No new domain package is created.** If this plan ever seems
to want one, the thing it wants belongs in an existing tier instead. What is being introduced is
strictly the presentation split *inside* tier 5:

| MVC | here | already exists? |
|---|---|---|
| Model | tiers 0–4, unchanged | yes |
| Controller | `web/*/controller.py` — orchestration, one function per route | no, currently inside the route |
| View | the DTOs — pydantic response models, plus `src/web/` | no, currently inline dict literals |

### Two standing conventions this deliberately overrides

Both are in `CLAUDE.md` and both would otherwise get this "fixed" back in six weeks, so they are
written down here and in an ADR rather than quietly broken.

1. **"No single-caller private helpers — inline them."** A route/controller split creates exactly
   one caller per controller function, on purpose. The justification is not decomposition for its
   own sake: it is that a route module becomes a readable *table of the HTTP surface* — 53 routes,
   their paths, their status codes, their response models, two lines each — and that table is the
   thing this codebase currently has no way to see.
2. **"One `try` per function."** Splitting a route means the `try` that maps `StoreError` → 502
   sits in one function and the work sits in another. The rule is kept in spirit by putting *all*
   exception→status mapping in the route layer and none in the controller: a controller raises
   domain errors, a route translates them. That is a sharper rule than the current mixture.

Nothing else in `CLAUDE.md` is relaxed. In particular the tier rule is untouched, and
`test_layering.py` needs no edit — it keys on top-level packages, and `web/` stays one package.

### How the code is written

- **Every route gets exactly a two-line docstring.** Line one: what it answers. Line two: what it
  refuses, or the one thing a caller would otherwise get wrong. Longer than two lines means the
  reasoning belongs on the controller or in the module docstring.
  ```python
  @router.post("/refs/{ref}/commit", response_model=CommitResult)
  async def commit(ref: str, body: ChangeSet, request: Request) -> CommitResult:
      """Land a verified change set on one ref, as a single commit. This is the deploy.
      Runs the gate again rather than trusting Save: staging is the browser's, so what arrives
      here is not provably what was checked."""
  ```
  (Two sentences on the second line is fine. Three lines of prose is not.)
- **Module docstrings stay as they are.** They are the best documentation in this repo and they
  move with the noun, not with the layer.
- **Controllers get the "what it answers / what it refuses / what it does not do" docstring** that
  the routes have today. The rationale currently sitting above a route body goes with the body.
- **No comment is deleted in a move.** A why-comment is attached to the code it explains; if that
  code goes to a controller, the comment goes with it.

---

## Part 1 — Decisions

### Settled

| Question | Decision |
|---|---|
| New domain packages | **None.** The model is tiers 0–4. |
| Tier of the new subpackages | Still `web` (tier 5). `test_layering.py` unchanged. |
| What a controller may import | Anything tiers 0–4, plus `web/support/`. **Never** a route module. |
| What a controller may raise | Domain exceptions and `HTTPException` where the refusal *is* HTTP (auth, 404 on an id). Status mapping for domain errors lives in the route. **Guarded mechanically**, not by good intentions — see below. |
| What a route may contain | Path, method, `response_model`, dependencies, the two-line docstring, one controller call, and exception→status translation. No `if` on domain state. |
| Response typing | `response_model=` on every non-streaming route. This is the point of the exercise. |
| Streaming routes | `StreamingResponse`, no `response_model`, controller returns the async iterator. Three routes: two turns and the job stream. |
| DTO module name | **`dto.py`**, not `schemas.py`. `schema` is taken twice already — `cube/schema.py` is the query schema and `schema_name` is a warehouse schema. |
| Request and response DTOs | One module per noun, both kinds in it. Requests are what exists today; responses are new. |
| `web/state.py`, `web/stream.py` | Move to `web/support/` unchanged. They are infrastructure, not a noun. |
| `mcp.Access`/`load`/`save` | **Leaves `web/` entirely** → `core/access.py`. It is a row in `analyst.db` and `web/` is the only tier that should never be doing SQLAlchemy. This is a layering bug the split exposes, not new scope. |
| The MCP tool definitions (`mcp.build`) | Stay in `web/` — they are an entry point, not a controller. `web/agentaccess/tools.py`. |
| Frontend | Updated in the same commit as any route rename. Never a compatibility shim; nothing is deployed. |
| Behaviour | Unchanged everywhere except where Part 5 renames a path. Any payload change is a bug in this migration, and Phase 0 exists to catch it. |

### Opened by reviewing this plan against the code, 2026-08-24 — **needs an answer before Phase 2**

**Who owns the connection body?** Five routes — `GET`/`POST`/`PATCH /api/connection`,
`POST /api/connection/probe`, `PUT /api/deployment` — all answer the same 16-key object, and
**12 of those keys are built by `evidence.public()` in tier 3**, not by `web/`. The remaining four
(`store_error`, `serving`, `serving_state`, `sandbox_reason`) are added by `connection.view()`.
So this is the largest DTO in the migration and the only one whose shape `web/` does not own.

Three ways, and none is free:

| | what it means | cost |
|---|---|---|
| **A** | A response DTO in `web/connection/dto.py` mirrors `evidence.public`'s dict by hand. | Two definitions of one shape, in two tiers, with nothing asserting they agree. It is the drift this migration exists to remove, reintroduced at its largest point. |
| **B** | `evidence.public` returns a typed object; the DTO is built from it. | Touches tier 3, which Part 9 forbids — and `evidence` is imported by `datamodel` and `agent`, so the change is not local to one call site. |
| **C** | Leave these five routes returning `dict` with no `response_model`. | Honest and cheap, and the payload snapshot still guards them. But the biggest object the browser reads stays untyped, and Phase 5 cannot generate a type for it. |

My recommendation is **C for Phase 2, then B as its own change** with its own decision, because B
is a tier-3 refactor wearing an HTTP hat and it deserves to be argued on its own terms rather than
smuggled in. Whichever way, the four keys `view()` adds are `web/`'s and get typed either way.

### Answered 2026-08-24

1. **Directory layout: feature folders** — Option A in Part 2. A noun is a directory.
2. **Route renames: #1, #2 and #3 only** — the three that fix a real hazard. #4 and #5 are
   consistency-only and are dropped; Part 5 keeps them recorded as rejected rather than pending.
3. **Generated frontend types: yes, after Phase 2.** With the stated stop condition — if the
   generated names read badly, `types.ts` stays hand-written and Phase 5 is abandoned, not forced.
4. **"Moving datamodel out" means the response shapes**, and `datamodel/` (tier 3) is not touched.

---

## Part 2 — Where the code goes

Today, 1849 lines in 11 flat modules:

```
web/  server.py 115   state.py 55    stream.py 65
      chats.py 120    files.py 195   results.py 51    dashboards.py 212
      connection.py 222   pipeline.py 140   model.py 473   mcp.py 201
```

### Option A — feature folders (recommended)

```
web/
  server.py              the app, the guard, the page. Mounts routers and nothing else.
  support/
    state.py             app.state, `idle`, `reloaded`, `cube`      (moved, unchanged)
    stream.py            sse, turn_stream                          (moved, unchanged)
    errors.py            StoreError/CubeError/SandboxError → status, in one place  (new)
  chat/       routes.py  controller.py  dto.py       question chats + the turn
  workspace/  routes.py  controller.py  dto.py       the sandbox's files (today files.py)
  result/     routes.py  controller.py  dto.py
  dashboard/  routes.py  controller.py  dto.py
  connection/ routes.py  controller.py  dto.py       the connection and the deployment
  pipeline/   routes.py  controller.py  dto.py
  model/      routes.py  controller.py  dto.py       served model, refs, files, editor chat
  agentaccess/routes.py  controller.py  dto.py  tools.py    today mcp.py
```

Nine directories, ~28 files. A reader who wants the model surface opens one folder and sees three
files in the order a request travels. This matches how `CLAUDE.md` already describes the surface —
"four nouns … a fifth prefix is a claim there's a fifth thing".

`model/` is the one that earns the split outright: 473 lines and four sections today, becoming
roughly `routes.py` 120 / `controller.py` 240 / `dto.py` 150.

### Option B — layer folders

```
web/  server.py  support/…
      routes/       chat.py workspace.py result.py dashboard.py connection.py pipeline.py model.py agentaccess.py
      controllers/  (the same eight names)
      dto/          (the same eight names)
```

Same 28 files, three directories. Closer to how "MVC" is usually laid out and to the literal
phrasing of the ask; the cost is that every change to one noun touches three directories, and
`routes/model.py` beside `controllers/model.py` beside `dto/model.py` is three tabs with the same
title.

**Recommendation: A.** The reason is the same one the module docstrings give for `model.py` being
one file today — a reader looking for the model surface should not have to guess. Feature folders
keep that property; layer folders trade it for a filesystem that looks like the diagram.

### What is deliberately *not* split

- **`server.py` stays one file.** It is the app, the token guard and the page. Splitting the
  middleware out would leave a reader asking which of two files decides who may reach the portal.
- **`support/stream.py` stays one file.** `sse` and `turn_stream` are one wire format at two
  altitudes; the module docstring already argues this.
- **No `service/` layer between controller and domain.** The domain tiers *are* the service layer.
  A controller that only forwards to `datamodel` is the correct amount of code.
- **No base controller class, no dependency-injection container, no repository pattern.** The
  stores are already modules reading `settings.db_path`.

---

## Part 3 — Naming

`CONTEXT.md`'s rule is that a name is taken once, so:

| new name | means | not to be confused with |
|---|---|---|
| `dto.py` | the wire shapes of one noun, in and out | `cube/schema.py` (query schema), `schema_name` (warehouse schema) |
| `controller.py` | orchestration for one noun's routes | `Runner` (jobs), `runner` (dashboards refresh) |
| `web/workspace/` | the **sandbox's** per-chat files | `datamodel/editing/workspace.py` — the model edit door. **This collision is real**; see Part 5, which renames the wire path too. If it stays, the directory is `web/chatfiles/`. |
| `web/agentaccess/` | the MCP surface | `agent/` (tier 4) |
| `support/` | infrastructure shared by every noun | `core/` (tier 0) |

Response DTO class names take the shape `<Noun><What>`: `ServedModel`,
`RefList`, `DiffReport`, `CheckVerdict`, `CommitResult`, `DashboardDetail`, `RefreshOutcome`,
`TableCatalog`, `JobSnapshot`, `ConnectionView`, `Reached`. Several of these names already exist in
`src/web/src/types.ts` — matching them on purpose, so the generated types in Phase 5 line up with
what the browser already calls things.

---

## Part 4 — The phases

Each phase is one commit or a short series, and each ends with `make test`, `make lint` and
`make web-build` clean. Phases 0–3 change no URL and no payload; the frontend is not touched until
Phase 4.

### Phase 0 — the safety net — **done 2026-08-24**

`tests/test_surface.py` (3 tests) and `tests/surface.json` (53 routes); `tests/test_payloads.py`
(2 tests) and `tests/payloads.json` (15 payloads); 7 tests filling the coverage gaps. 585 tests
pass; `make lint` and `make web-build` clean.

**The payload snapshot was missing from the first draft of this phase, and that was the plan's
worst hole.** As written, Phase 0 recorded only each route's *declared schema* and Phase 2 was
supposed to lean on it to prove no payload changed. It cannot: adding `response_model` changes the
declared schema **by design**, so the one artefact meant to catch an accidental change is the one
artefact guaranteed to be full of expected noise. `tests/test_payloads.py` is the missing half — it
calls the routes and records the JSON, so a key dropped, renamed, coerced or newly `null` fails
with a diff of the payload itself.

Volatility there is recognised by *value*, not by key name: a 32-hex string becomes `<id>` and any
number past the epoch becomes `<time>`, so `orders.count` and `orders.status` stay visible. A
key-based rule would have blanked every `id` in the payload, including the member ids — which are
exactly the content a bad response model would mangle.

Three further notes, none of which change the plan:

- **`starlette` 1.3.1 → 1.6.0 and `uvicorn` 0.52.1 → 0.52.4.** FastAPI is already the newest
  release (0.141.1), so the `_IncludedRouter` wrapper `declared()` walks is current behaviour and
  not something an upgrade removes. FastAPI puts no ceiling on either package.
- **The surface is written down in three places now**: `surface.json`, the hand-listed set in
  `test_every_path_the_browser_calls_is_a_real_route`, and `sources.ts`. Phase 4 should delete the
  middle one — `surface.json` covers it and covers more.
- **`declared()` reads a semi-private attribute** (`original_router`), so
  `test_the_walk_sees_every_route_the_document_does` cross-checks it against the public OpenAPI
  document and asserts a floor of 40 routes. Without that, a framework change would leave the walk
  returning two routes and every assertion below it passing vacuously.
- **The `api` fixture moved from `test_http.py` to `conftest.py`**, along with `handing_over` and a
  new `opening` fixture, because two modules drive the surface now. `tests/` is not a package, so a
  shared helper has to be a fixture — the same reason `model_store` hands over a class.

The original specification of this phase follows.

#### As specified

~25 of the 53 routes have no test that calls them (`model/*` beyond `/summary` and `/commit`,
`pipeline/*` beyond its 404s, `results/*`, `mcp/*`). Restructuring them blind is the main risk in
this plan, so:

- **`tests/test_surface.py`** (new): walks `server.app.routes` and asserts the sorted list of
  `(methods, path, name)` against a literal in the test. Any accidental rename, drop or
  path-parameter change fails here with a diff instead of in the browser.
- **The same test snapshots `app.openapi()`'s paths and response schemas** to
  `tests/surface.json`, regenerated by `pytest --snapshot-update`. In Phase 2 this is what proves
  `response_model` did not change a payload; in Phase 4 it is the record of what the renames did.
- **Fill the worst coverage gaps *before* moving code**, not after: `GET /api/model` with a fake
  catalog, `GET /api/model/refs`, `POST /api/model/check`, `GET /api/pipeline/tables`,
  `GET /api/results/{id}` and its CSV, `GET /api/mcp`. Roughly 10 tests. These are worth having
  regardless of this migration.

Exit: the route table and the OpenAPI snapshot are committed and green.

### Phase 1 — infrastructure, no route touched

- `web/state.py` → `web/support/state.py`, `web/stream.py` → `web/support/stream.py`. Import-only
  change; both files unchanged.
- `web/support/errors.py`: the exception→status mapping that is currently written out five times
  (`_store`, `_sandbox`, `model.STATUS`, the `CubeError` arms in `dashboards`, `connection`'s
  `RuntimeError` → 503). One `status_for(exc)` plus one `@asynccontextmanager` per service.
- `mcp.Access`/`load`/`save` → `core/access.py`. `web/agentaccess` reads it; `server.py`'s
  lifespan calls it. **`web/` stops importing `sqlalchemy` entirely** — grep is the check.

Exit: `tests/test_surface.py` unchanged and green. One import fixed in `tests/test_http.py`
(`from analyst_agent.web import server, state` → `from analyst_agent.web.support import state`).

### Phase 2 — DTOs, routes stay put

Per noun, in this order (smallest first, so the pattern is settled before the hard ones):
`result`, `agentaccess`, `pipeline`, `chat`, `workspace`, `dashboard`, `model`, `connection`.

**`connection` moved from sixth to last** on review: it is not a medium-sized noun, it is the one
whose shape another tier owns, and doing it early would settle that question by accident. See the
open decision in Part 1.

Two request bodies need attention while here, both found by reviewing rather than by reading the
plan:

- **`POST /api/dashboards/{id}/layout` takes `body: dict`** — the only untyped request left. No
  `extra="forbid"`, so a renamed field in the browser lands as a silently ignored key rather than a
  422, which is precisely the failure `ChangeSet` was given `extra="forbid"` to prevent. It gets a
  request DTO in Phase 2.
- **`dashboards.layout.validate` stays as it is.** It is ~40 lines of hand-rolled checks and the
  known counter-example to "validate with pydantic, not by hand" — but the agent's `set_layout`
  tool calls the same function, so converting it is a change to shared domain validation, not to a
  route. Reported, not refactored, per Part 0.

For each: create `dto.py`, move the request models into it, write a response model for every route
by reading the dict literal it returns, and add `response_model=`. The route body still builds the
value, now as `ServedModel(...)` instead of `{...}`.

**This is where a payload can silently change**, so Part 6's traps apply here more than anywhere.

Exit: the OpenAPI snapshot now has real schemas; the *response payload* snapshot is byte-identical
for every route the tests exercise.

### Phase 3 — controllers

Per noun, same order. Move the body of each route into `controller.py`, leave the route as
signature + two-line docstring + one call + exception translation. The shared private helpers move
with their noun: `_store`/`_impact`/`_editable` → `model/controller.py`, `_checks` →
`dashboard/controller.py`, `_read` → `workspace/controller.py`, `_sandbox` → `support/errors.py`.

Exit: no route function longer than ~12 lines; `web/*/routes.py` reads as a table of the surface.

### Phase 4 — route renames and the frontend

One commit per noun, backend and `src/web/` together, because there is no compatibility window to
keep. See Part 5 for the proposed set. `tests/test_surface.py` and `surface.json` are updated in
the same commit — that diff *is* the changelog for the wire format.

### Phase 5 — generated frontend types (optional, decision 3)

`openapi-typescript` over the app's own `openapi.json`, emitted to
`src/web/src/api/generated.ts`, with a `make web-types` target and a CI check that it is current.
Deletes most of `types.ts` (282 lines of hand-written mirrors) and makes browser/server drift
impossible rather than merely unlikely. `sources.ts` and `api.ts` keep their hand-written
docstrings and helpers and import the generated types.

Worth doing only after Phase 2, and only if the response models turn out to be as clean as they
should be. If the generated names are ugly, stop — a hand-written type that matches is better than
a generated one nobody can read.

### Phase 6 — documentation

- `CLAUDE.md`: rewrite the "Web portal" subsystem section for the new layout; add the two
  overridden conventions to the layering rule so the next reader knows they were deliberate.
- `CONTEXT.md`: `dto`, `controller`, and the `workspace` collision from Part 3.
- `docs/adr/0004-http-is-routes-controllers-dtos.md`: why single-caller controllers are allowed
  here, why no service layer, why DTOs and not dicts, and what was rejected (a `models/` package,
  a repository layer, per-route files).

---

## Part 5 — Proposed route renames

Ordered by how much they are worth. Each row is independent; take any subset.

| # | today | proposed | why | frontend |
|---|---|---|---|---|
| 1 | `POST /api/pipeline/{stage}` | `POST /api/pipeline/runs` with `{stage, …}` in the body | A bare catch-all segment sits beside `/tables` and `/job` and shadows anything added under the prefix later — `results.py` already carries a comment about this exact class of bug one layer down. Moving the stage into the body replaces a hand-written 404 with an enum on the DTO. | 1 call site |
| 2 | `POST /api/model/check`, `POST /api/model/commit` | `POST /api/model/refs/{ref}/check`, `…/commit` | Both already carry `ref` in the body and both act on exactly one ref; `/refs/{ref}/merge` and `/refs/{ref}/diff` are already shaped that way. Puts the three ref-scoped writes under one parent and turns `_editable(ref)` into a path dependency. | 2 |
| 3 | `/api/chats/{id}/files/…`, `/api/chats/{id}/preview/…` | `/api/chats/{id}/workspace/files/…`, `…/workspace/preview/…` | "files" means two different things on the wire today: the sandbox's uploads and the model's YAML (`/api/model/files`). This is the `CONTEXT.md` name-taken-once rule broken in the URL space. Also ends the `{path:path}`-under-`{chat_id}` ordering hazard. | 4 |
**Rejected 2026-08-24, having been considered:** `POST /api/model/chats` →
`/api/model/refs/{ref}/chats`, and `POST /api/connection/probe` → `/api/deployment/probe`. Both
are consistency-only — neither fixes a hazard — and a rename that buys nothing is churn in a diff
that already changes seven call sites.

**Not renamed, deliberately:** `/api/connection` vs `/api/deployment` (the two-step split is
documented and load-bearing), `/api/model/summary` (it is the chat page's cut-down model, and
"summary" is the honest word), `/api/pipeline/tables` (the comment explaining why it is not
"catalog" is one of the better ones in the repo).

---

## Part 6 — Traps, stated rather than discovered

1. **`tests/test_http.py` monkeypatches `state.cube`.** It works because routes call
   `state.cube(app)` through the module. A controller that does `from ..support.state import cube`
   binds the function at import and the patch stops applying — the fixture silently hands the real
   accessor a tmp database with no deployment, and every affected test 503s. **Controllers import
   the module, never the function.** Same for `state.idle`/`state.reloaded`.
2. **`response_model` changes payloads.** It drops unknown keys, coerces types and, by default,
   *includes* fields that are `None`. Today several routes return `x and asdict(x)` — `None` or a
   dict. A response model must declare those `Optional` and must not gain a field the browser does
   not read. The Phase 0 payload snapshot is the only reliable check; do not eyeball it.
3. **`asdict()` output is the current contract.** `impact.Impact`, `catalog.counts()`,
   `delta.Change`, `Reached.as_dict()` — the field names in those dataclasses are on the wire
   today. A response model has to match them exactly, including any field the browser ignores, or
   Phase 5's generated types will disagree with `events.ts`.
4. **Route order matters within a prefix, and file order changes it.** `/api/pipeline/{stage}`
   must stay *below* `/tables` and `/job`: a parameter segment registered first swallows every
   fixed sibling at its depth. `test_every_route_is_reachable_and_unshadowed` now asserts this
   for the whole app, and it was checked against three synthetic shadowing cases before being
   relied on, so it is known to fail rather than merely to pass.

   **Corrected while building Phase 0:** an earlier draft of this trap claimed the two
   `include_router` calls sharing `/api/chats` (`chat` and `workspace`) were order-dependent.
   They are not. `{path:path}` there sits under a literal `files/` segment, so it cannot reach
   `/messages` or `/events` whichever router is included first — swapping them shadows nothing.
   Rename #3 is still worth doing, for the name collision, but not for this.
5. **`state.idle(app)` runs *before* validation in several routes** (`connect`, `create_ref`,
   `run_stage`). That ordering is deliberate — a busy job refuses first — so it stays in the route
   layer, not the controller, or the refusal order flips.
6. **`web/model.py`'s `STATUS` dict is the workspace's kinds mapped to HTTP.** It belongs to the
   model noun, not to `support/errors.py`: the kinds are `datamodel.editing`'s vocabulary.
7. **`POST /api/model/check` answers 200 for a rejected model.** That is documented and
   intentional. A refactor that "tidies" it into a 400 breaks the editor's one-banner rendering.
8. **The MCP mount is not a router.** `app.mount("/mcp", mcp_app)` plus the guard's `/mcp` branch
   plus the lifespan entering the mounted app's own lifespan — that whole dance stays in
   `server.py` and is the reason `agentaccess` has a `tools.py` rather than a controller.
9. **`files.py` reads `transcript` to check a chat exists but owns nothing in the database.** The
   controller keeps that check; it is not a layering smell, it is a 404.
10. **"Status mapping lives in the route" will erode within a week unless something checks it.**
    Raising `HTTPException` from a controller is always the shorter path, and nothing about the code
    stops it — a rule with no test is a comment. Phase 3 adds one, in the shape this repo already
    uses for the tier rule: parse `web/*/controller.py` and assert that `HTTPException` appears
    only in an allowlist of controllers where the refusal genuinely is HTTP (`workspace`'s 404 on
    an unknown chat, `chat`'s 404, `model`'s `_editable`). An allowlist that starts growing is the
    signal to drop the rule honestly rather than keep pretending.
11. **The `model` routes are monkeypatched by name in four tests** — `model_files`,
    `served_catalog`, `open_store` are patched as attributes of `web.model`. Phase 3 moves the code
    that calls them into `web/model/controller.py`, so those four patch targets move with it. This
    is the same trap as #1 seen from the test side, and it means Part 7's claim needs qualifying.

---

## Part 7 — Tests

| test | change |
|---|---|
| `tests/test_surface.py`, `tests/surface.json` | **Done**, Phase 0. The route table and each route's declared schema. |
| `tests/test_payloads.py`, `tests/payloads.json` | **Done**, Phase 0. What the browser actually receives. This is the one that guards Phases 1–3. |
| `tests/conftest.py` | **Done**, Phase 0. Gained the `api` fixture and `opening`, now that two modules drive the surface. |
| `tests/test_http.py` | 7 new tests in Phase 0. In Phases 1–3: the `state` import, and **the four monkeypatch targets that move with the code** (`web.model` → `web/model/controller.py`) — see trap #11. Its assertions do not change; if one has to, that is a behaviour change and the phase is wrong. The renamed paths land in Phase 4, along with deleting the hand-listed set in `test_every_path_the_browser_calls_is_a_real_route`. |
| `tests/test_layering.py` | Unchanged, and it should stay unchanged. |
| Everything else | Unchanged. Nothing below tier 5 moves. |

The repo rule holds: no containers, no model calls. Every DTO is asserted through the app with the
existing fakes.

---

## Part 8 — Risks

1. **A payload changes and nothing notices**, because a route the tests never call now returns
   `null` where it returned `{}`. Mitigated by Phase 0 and by fixing the coverage gaps first — but
   the four `model/*` and two `mcp/*` routes that only a live deployment exercises stay a real
   exposure. They get read against the browser code by hand.
2. **53 two-line docstrings written in one sitting become 53 restatements of the function name.**
   The docstring's second line has to say something a reader could not have guessed. Where there
   is nothing to say, the honest version is one line, and that is allowed.
3. **The split makes small nouns look over-engineered.** `result/` is 51 lines becoming three
   files. It is worth it only for consistency of layout — and if the answer after Phase 3 is that
   `result` and `agentaccess` read worse than before, they should be collapsed back to one module
   each and the plan should say so in Part 9.
4. **Phase 4 is the only irreversible-feeling one**, since it changes URLs the browser holds. It
   is also the cheapest to revert: nothing is deployed, and the frontend commit is in the same
   diff.
5. **Scope creep into the domain tiers.** Reading 1849 lines of route code will turn up things
   that belong lower down (the `mcp` SQL is already one). The rule is Part 0's: fix the layering
   bug the split *exposes*, and report anything else instead of fixing it.

---

## Part 9 — Explicitly not in this plan

- Any change to tiers 0–4 beyond moving `mcp.Access` into `core/`.
- Auth, the token guard, or the MCP bearer flow.
- Anything about the agent, the gate, the sandbox or the store.
- A versioned API (`/api/v1/…`). Nothing consumes this but the bundled frontend and the MCP
  clients, and a version prefix would be a promise to keep the old shape.
- Splitting `src/web/` into its own MVC. The browser is already components + `api.ts`/`sources.ts`
  + `types.ts`, and Phase 5 is the only change proposed there.

---

## Part 9a — Payload changes, one line each

Phases 1–3 are supposed to change no payload, so every exception is recorded here with the
consumer that was checked. An entry that cannot name what reads the field does not belong in the
list; it belongs in a revert.

| route | was | is | why it was accepted |
|---|---|---|---|
| `GET /api/pipeline/job`, no job running | `{"id": null}` | all seven fields at their defaults | The route answered a one-key object when idle and a seven-key object when busy — two shapes from one route, which is what typing it exists to end. `src/web/src/sources.ts` already declares all six of the others optional and `BuildModel.tsx` reads `job?.done` and `job.id` defensively, so `done: false` behaves exactly as the absent `done` did. Phase 5 can make those six required in the generated type. |

## Part 10 — What was actually built

To be filled in after Phase 3, in the shape of `editor-and-onboarding.md`'s Part 11: what matched
this plan, every deviation with its reason, and what is still unverified.
