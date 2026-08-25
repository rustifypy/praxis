# Onboarding builds the model, and the model gets a real editor

> **Partly superseded.** The editor described here staged in the browser and posted change sets.
> A branch being edited now has a draft on disk — see `docs/adr/0003` and the editing section of
> `CLAUDE.md`. The onboarding half still holds.

Agreed and built 2026-08-22. This file is the specification; **Part 11 records where the tree
deviates from it.** What survives of the rest belongs in `CLAUDE.md` and `CONTEXT.md` — much of it
is there now — and the reasoning belongs in the commits.

Two changes, one release:

1. **Onboarding absorbs the pipeline.** The wizard ends with a model Cube is serving, and the
   stage-by-stage pipeline UI on the data page is deleted. Building a model stops being something
   you discover on an admin screen after setup and becomes the last thing setup does.
2. **The model editor becomes a real editor.** A branch bar, a file tree, a syntax-highlighted
   editor, hunk-by-hunk review of what the chat proposes, and a two-stage `Save` →
   `Commit & sync` so nothing reaches the deployment until it has compiled.

---

## Part 0 — How the code for this is written

**These rules override the general style in `CLAUDE.md` and `CONTRIBUTING.md` for everything
written under this plan.** They are here because this is a large change across four subsystems,
and the next person to read it will not have this document.

### Docstrings

- **Every module gets a docstring.** Say what the module *is*, what it is *not*, and which
  decision a reader would otherwise try to undo. If a module exists because of a Cube, git or
  HTTP behaviour, that behaviour is named in the docstring.
- **Every public function, class and dataclass gets a docstring**, and it answers three
  questions in this order: what it answers, what it refuses, and what it deliberately does not
  do. The third is the one that saves an hour — `workspace.verify` not committing is the whole
  point of it, and a reader who assumes it does will write the Save button wrong.
- **Every React component gets a block comment above it** in the style already used by
  `Setup.tsx`, `ModelEditor.tsx` and `Deploy.tsx`: what the component owns, what state is
  authoritative, and which layout decisions are load-bearing rather than taste.
- A `__init__.py` that re-exports gets a docstring saying why the package is one package.

The house style to match, exactly, is `src/analyst_agent/dashboards/__init__.py`,
`src/analyst_agent/sandbox/__init__.py`, and the module docstring on
`src/analyst_agent/web/model.py`. Match their density. Do not write shorter than those.

### Comments

- **Comment the *why*, verbosely, and never the *what*.** `# tiers 1-2, then the write` is
  useless; `# The gate runs before the write because the write is the deploy: Cube asks the
  store for this ref's version on every request, so a commit is answering within seconds and
  there is no restart afterwards to hide behind.` is the standard.
- **Every constant gets a comment saying how its value was chosen** and what breaks on each
  side of it. A number with no comment is a number nobody can ever change.
- **Every refusal gets a comment saying what the permissive version broke.** Every guard in this
  plan exists because something specific went wrong; say which.
- **Every `except` clause says why that exception class and not a broader one.** `httpx`
  transport errors do not subclass `OSError`; a `StoreError` escaping `workspace` 500s a route.
  Those facts go beside the clause.
- Do not narrate a sequence of steps in comments. If the code reads `create → write → ask →
  delete`, the comment says why it is a throwaway ref, not that four things happen.

### Simplicity — this is the part that gets violated

- **A sequential process stays one function.** Do not split a linear passage into hops so that
  each function fits on a screen. `workspace.verify` deleting a stale preview ref, creating one,
  writing to it, asking Cube, checking impact and deleting it again is **one function**, because
  it is one thing that happens once in one order, and a reader following a Save needs to read it
  top to bottom in one place.
- **No single-caller helpers. None.** If a function has one call site, inline it. This is already
  a project convention and it is the one most often broken while writing new subsystem code.
- **Before writing any new function, state its callers.** Two or more, or it does not exist.
  The exceptions in this plan are named explicitly in Part 2; anything not named there needs an
  argument in the pull request.
- **No new module, package, class or wrapper beyond what Part 2 names.** Part 2 is deliberately a
  short list, and the fact that it adds *zero new Python modules* is the point, not an accident.
- **Validate with pydantic, not by hand.** Request bodies are pydantic models with
  `extra="forbid"` where the shape is closed. Do not write chains of `isinstance` and key checks;
  convert `ValidationError` into the message list where errors must come back as text.
- **Readability beats modularity, and both beat cleverness.** Where the two conflict, write the
  longer, flatter, more obvious version and comment why it is not factored.
- **Report violations, do not refactor them.** Existing code that breaks these rules is left
  alone and mentioned in the pull request, unless correctness requires touching it.

### Naming

- **A name is taken once.** Grep before naming. `check` already means tiers 1–2 in
  `editing/edits.py` and tiers 1–3 in `modelgen/publish.py`; that is why the new Save path is
  `workspace.verify` and not a third `check`.
- Where a concept has two names in two systems, both names appear in the docstring. The UI word
  is **branch**; the code word is **ref**. Say so wherever they meet.

---

## Part 1 — Settled decisions

| Question | Decision |
|---|---|
| Where unsaved and saved-but-unpushed edits live | **The browser.** No table, no directory. A refresh discards them and the deployment is re-read. |
| What `Save` operates on | **The whole dirty set at once.** A multi-file change is one unit; it compiles as one or it is refused as one. |
| What `Save` checks | Tiers 1–2 (YAML shape, append-only cubes, view shape) **and** tier 3 — Cube compiles the proposed model on a throwaway ref — **and** the impact on dashboard tiles. |
| What `Commit & sync` does | Writes the staged set to the branch ref as one commit. Cube serves it within seconds. |
| Onboarding: table choice | One schema, chosen from what `read` found, preselected when there is only one. Tables picked within it. Multi-schema is not built. |
| Onboarding: enrichment | `cubes` is mandatory. Descriptions and views are a checkbox, on by default, skippable — and afterwards reachable through the editor chat rather than a stage button. |
| `onboarded` means | Both addresses stored **and** cubes present in the store. Computed in `evidence`, never a column. |
| Branch bar scope | The data page only. Chat, boards and agents are hardcoded to `main` and show no branch. |
| Chat-proposed edits | Reviewed hunk by hunk in the editor pane, accepted into the buffer, and only then saved. The agent commits nothing. |
| The editor | CodeMirror 6 with YAML highlighting; `@codemirror/merge` for the hunk review. |
| Merging | Refused when it would break a tile, with an explicit override. Deleting the branch afterwards is a checkbox in the same call. `main` is never editable and never deletable. |
| If the model comes out wrong | One **Rebuild the model** action behind a typed confirm, which re-runs the whole pipeline through the same screen onboarding used. |
| Deleted outright | The `adopted` model concept, following a moved deployment, resetting the pipeline, rotating the secrets. |

### Vocabulary change

`CONTEXT.md` currently lists **branch** as a forbidden synonym for **Ref**. That entry is
amended: a **Ref** is what the store and the code call it, **branch** is what the UI calls it,
and the glossary says both rather than contradicting the screen.

---

## Part 2 — Where the code goes, and what is deliberately not split

This section is the structure review. Read it before writing anything.

### Zero new Python modules

Everything on the backend lands in a file that already exists. That is the readability decision,
and these are the three splits I considered and rejected:

- **`datamodel/editing/preview.py` — rejected.** The preview-ref compile folds into
  `workspace.py`. `workspace` is already "the one door every edit goes through", and a fourth
  module in `editing/` would leave a reader asking which of two files refuses an edit. The
  passage is sequential and has one shape; it is one function in the module that already owns
  refusals.
- **Splitting `web/model.py` — rejected.** The HTTP surface is four nouns and one module per
  noun. `GET /api/model` and `POST /api/model/commit` are the same thing seen from two ends, and
  a reader looking for either must not have to guess the file.
- **A `staging` module — rejected, because there is no server-side staging.** The whole point of
  the browser-memory decision is that no Python owns a buffer.

| File | Change |
|---|---|
| `datamodel/editing/workspace.py` | Grows. `write`/`delete` (single path) are replaced by `stage` (tiers 1–2 over a change set), `verify` (tiers 1–3 plus impact, on a throwaway ref, committing nothing) and `commit` (tiers 1–2 then one commit). |
| `datamodel/editing/edits.py` | Untouched. It is pure and already set-shaped. |
| `datamodel/editing/impact.py` | Untouched. |
| `datamodel/evidence.py` | `adopted` and `invalidated_by` deleted. `public` gains a computed `onboarded`. |
| `core/deployment.py` | `onboarded` property, `reset_progress` and `drop_steps` deleted. |
| `core/events.py` | One new `EventType`: `proposal`. |
| `agent/tools/editor.py` | `write_model_file` → `propose_model_file`; `delete_model_file` → `propose_delete`. `EditContext` gains the staged overlay. Neither tool touches the store. |
| `agent/prompts.py` | `EDITOR` rewritten: nothing you write is saved, the user accepts and saves, you cannot commit or merge. |
| `web/model.py` | `POST /check`, `POST /commit`, merge gains `delete_after`, `GET /files` reports each path's kind. `PUT`/`DELETE /files/{path}` and `POST /validate` deleted. |
| `web/pipeline.py` | `DELETE ""` deleted. Everything else stays — it is the wizard's backend now. |
| `web/connection.py` | `POST /deployment/secrets` deleted. `PUT /deployment` stays, because the wizard needs it, and nothing outside the wizard calls it any more. |

### Frontend

| File | Change |
|---|---|
| `components/BuildModel.tsx` | **New.** Schema picker, table picker, the enrichment checkbox, the job and its progress. Two callers — the wizard's last step and Rebuild — which is what earns it a file. |
| `components/Setup.tsx` | Step 4 added, rendering `BuildModel`. |
| `components/ModelEditor.tsx` | Rewritten. **Owns the branch bar, the tabs and the edit buffer**, because Save and Commit act on that buffer and one component holding all three is one state owner. Lifting the buffer into `SourceDetail` was considered and rejected: the data page would then hold state it never reads. |
| `components/FileTree.tsx` | **New.** `cubes/` and `views/` as collapsible groups, with a dirty marker per path. |
| `components/CodeEditor.tsx` | **New.** The whole CodeMirror lifecycle — plain YAML mode and `unifiedMergeView` for a pending proposal — behind one component, because a CodeMirror instance is imperative and mixing its setup into a component that also renders a toolbar is how the two get tangled. |
| `components/MergeDialog.tsx` | **New.** The diff, the impact report, the delete-the-branch checkbox, the override. |
| `components/Proposal.tsx` | **New.** The transcript's card for a proposed file — "cubes/order.yml · +3 −1 · Review". Its own file because that is already the pattern (`Approval.tsx`, `FileBlock.tsx`, `ToolCall.tsx` are one file per item kind). |
| `components/EditorChat.tsx` | Ships the staged buffer with each message; surfaces proposals to its parent. |
| `components/SourceDetail.tsx` | Slimmed to the connection header, the banners, the menu (Edit connection, Rebuild the model) and `ModelEditor`. |
| `components/Deploy.tsx` | **Deleted.** |
| `components/TablePicker.tsx`, `JobProgress.tsx` | Unchanged files, new callers — both move under `BuildModel`. |
| `sources.ts`, `types.ts`, `events.ts` | `check`, `commit`, `deleteRef`, merge's `delete_after`, the `proposal` item; `rotate`, `resetPipeline`, `adopted`, `invalidates`, `writeModelFile`, `validateModel` deleted. |

### Where the diff is reviewed, and why not in the transcript

The transcript shows a **card** per proposed file; the **hunks** are accepted in the editor pane.
Rendering an accept/reject diff in two places means two implementations of "apply a hunk", and
the second one is always the one that produces YAML neither side wrote. One renderer,
`CodeEditor.tsx`, owns hunks.

### The `/cube/` folder at the repo root

`cube/model/{cubes,views}` is gitignored and left over from the local model directory
this repo deliberately deleted. Nothing reads it. It plays no part in this plan — with staging in
the browser there is no local mirror of the model at all — and it should be deleted by hand.

---

## Part 3 — Phase 1: compile without committing

Everything else leans on this, so it lands first and alone.

### `workspace.resolve`

Accepts `cubes/*.yml` and `views/*.yml`.
store's own `ALLOWED` already permits only markdown there; this refusal exists so the message a
person reads says why instead of `400`.

### `workspace.stage(base, proposed)`

Tiers 1–2 over a whole change set: YAML parses, cubes are append-only against **main** (never
against the ref's own previous state), views have a shape Cube will accept. Markdown is excluded
before `edits.check` sees it. Returns the existing `Outcome`, because a workspace failure is an
`Outcome` and never an exception — one escaping here would 500 the route and kill a turn.

### `workspace.verify(ref, write, delete)`

**One function, top to bottom, no helpers.** Tiers 1–2, then, when any `.yml` changed:

- delete a stale `preview` ref if one is there (a crashed verify leaves one behind, the same
  defensive delete `publish.install` does on the way in),
- create `preview` from `ref`,
- write the change set onto it,
- `publish.check(ref=PREVIEW)` — Cube compiles it and probes a measure per entity,
- `impact.check` against that compiled catalog,
- delete `preview`.

Guarded by a module-level `asyncio.Lock`, because there is one ref name and two concurrent Saves
would race on it. A markdown-only change set skips the whole Cube leg — Cube ignores `.md`, so
creating a ref to prove it would be a ten-second no-op.

Returns the validation and the impact. **It commits nothing to the branch**, and its docstring
says so first, because the Save button is built on that being true.

### `workspace.commit(ref, write, delete, message)`

Tiers 1–2 again — the browser may have changed since Save, and the gate runs before the write
because the write is the deploy — then one `store.edit`. One commit for the whole set, so the
history reads as one change rather than as five.

### Routes

- `POST /api/model/check` → `{ref, write, delete}`, answers validation + impact + tier-1-2
  errors and warnings. This is Save. It is also Validate: an empty change set verifies the ref as
  committed, which is why the old `POST /validate` is deleted rather than kept beside it.
- `POST /api/model/commit` → the same body plus a message. This is Commit & sync.
- `POST /api/model/refs/{ref}/merge?force=&delete_after=`.
- `GET /api/model/files?ref=` reports `{path, kind}` per file so the tree can group.
- Deleted: `PUT /api/model/files/{path}`, `DELETE /api/model/files/{path}`, `POST /api/model/validate`.

---

## Part 4 — Phase 2: onboarding builds the model

### `onboarded` moves off the row

`core.db.SCHEMA` cannot gain a column — `CREATE TABLE IF NOT EXISTS` does not diff, so a new
column silently does not appear on an existing `analyst.db` and there is no migration mechanism.
So `Deployment.onboarded` is deleted and `evidence.public` computes it: both addresses stored and
`Evidence.done("cubes")`. Read off the cubes actually in the store rather than off a recorded
step, which is the same argument `Evidence` already makes everywhere else.

### The wizard's fourth step

`Setup.tsx` keeps its shape — the step is derived from the row, never from a cursor, so a reload
lands where you left it. Step 4 renders `BuildModel`, which:

1. Runs `read` as a job on arrival and streams it. `read` caches a catalog and touches no model
   files, which is why it can be fired without asking.
2. Shows the schema dropdown (preselected when one schema was found) and `TablePicker` beneath it.
3. Offers **also write descriptions and views with AI**, checked by default, labelled with its
   real cost — one model call per table — and with the fact that the editor chat can do it later.
4. Runs `cubes`, then `describe` and `views` when checked, as one job with `JobProgress` under it.
5. `cubes` failing blocks the step and offers Retry. `describe` or `views` failing does **not**:
   the model is up and answering, so it reports what failed, points at the editor chat, and lets
   you through.

`publish.install` is untouched. It already builds the files, commits them to a `candidate` ref,
has Cube compile that ref, and merges into `main` only if Cube accepted — which is exactly
"write the model, then push it to main where the deployment lives", and is why a model Cube
refuses never replaces a working one.

### Rebuild

One menu item on the data page behind a typed confirm, rendering the same `BuildModel`. The
store's history is the undo: `publish.install` replaces the whole model in one commit, so what
was there is the parent commit and `GET /diff` shows what the rebuild changed.

---

## Part 5 — Phase 3: the agent proposes, it does not write

Today `write_model_file` commits and Cube serves it within seconds, so there is nothing to
approve. That is the behaviour being replaced.

- `propose_model_file(path, text)` runs tiers 1–2 against main plus the overlay — so the agent
  still gets its append-only refusals in a form it can act on — writes into an in-memory overlay
  on `EditContext`, and emits a `proposal` event. It touches no ref.
- `propose_delete(path)` is the same shape.
- `read_model_file` reads **overlay first, then the store**, so a second proposal builds on the
  first instead of silently reverting it.
- `validate_model` compiles the overlay through `workspace.verify`. It stays, because the agent's
  only compile signal is now this call — a proposal that will not compile is otherwise discovered
  by the human at Save.
- **The browser ships its staged buffer with every message.** `POST /api/model/chats/{id}/messages`
  takes `{question, staged}` and the turn seeds the overlay from it. Without this the agent reasons
  over the committed ref and its next proposal quietly discards the edit you accepted a minute ago.

### The `proposal` event

A new `EventType`, and **the second event type that carries a body** — `approval` is the first.
The reason is the same: the moment before acceptance is the only one where the content exists.
Unlike a script there is no file on disk to point at, because nothing was written, so it is
stored and a replayed transcript can still render the diff. `EVENT_KEYS` for the editor tools
stays body-free; the body is on `proposal`, not on `tool_end`.

---

## Part 6 — Phase 4: the editor

New frontend dependencies: `codemirror`, `@codemirror/lang-yaml`, `@codemirror/merge`. The last
is why hunk-by-hunk review is affordable — `unifiedMergeView` ships accept and reject controls
per chunk, so no diff algorithm and no hunk re-applier gets written here. Confirm its API against
the current docs before building on it.

```
┌─ branch bar ───────────────────────────────────────────────────────┐
│ ⎇ feature-x ▾  [+ New branch]      2 unsaved  [Save] [Merge…]      │
├─ tabs ─────────────────────────────────────────────────────────────┤
│ Files │ Served model                                               │
├──────────────┬──────────────────────────────┬──────────────────────┤
│ FileTree     │ CodeEditor                   │ EditorChat           │
│  cubes/      │  YAML, or unifiedMergeView    │  proposes into the   │
│  ● order.yml │  with ✓/✗ per hunk            │  buffer; commits    │
│  views/      │                               │  nothing             │
└──────────────┴──────────────────────────────┴──────────────────────┘
```

- The buffer is `Map<path, {base, text, staged}>` in `ModelEditor`. `dirty` → the button reads
  **Save**; `staged` → **Commit & sync**; clean → neither is offered.
- **On `main` the tree is read-only and there is no Save, no Commit and no chat.** `main` is
  generated and never edited directly; `workspace.editable` is the one rule and both the routes
  and the tools go through it.
- Save posts every dirty path at once. A clean verdict marks them staged; errors land in one
  banner and nothing moves.
- Commit & sync posts the staged set, then refreshes the tree, the served model and the branch's
  changed count.
- Merge opens `MergeDialog`: the patch, the impact report, *delete this branch after merging*,
  and — only when something would break — an explicit override.
- **A refresh discards unsaved and staged work.** The bar says so in words rather than letting
  someone find out. Two tabs on one branch will fight, and that is accepted: one person, one
  portal.

---

## Part 7 — Deletions

| Deleted | Why |
|---|---|
| `components/Deploy.tsx` | Following a moved deployment is gone; the wizard has its own hand-over. |
| The stage stepper, the stage panel, the `invalidates` warning in `SourceDetail.tsx` | The pipeline is onboarding's, not an admin screen's. |
| `evidence.adopted`, its field on the connection body, its banner | It exists only to describe a store that arrived with a model in it. |
| `evidence.invalidated_by`, `deployment.drop_steps` and their two call sites in `steps.py` | They warn about re-running one stage out of four. There is one Rebuild now, and it runs all of them. |
| `POST /api/deployment/secrets`, its confirm, `sources.rotate` | Rotating requires re-running both compose files: a re-deployment, not a portal action. |
| `DELETE /api/pipeline`, `deployment.reset_progress`, its confirm, `sources.resetPipeline` | Its purpose was re-pointing at a different database. |
| `PUT /api/model/files/{path}`, `DELETE /api/model/files/{path}`, `POST /api/model/validate` | Replaced by `check` and `commit`, which are set-shaped. |
| `Deployment.onboarded` | Computed in `evidence` now. |

`modelgen/` and `/api/pipeline` **stay in full.** Only the stage-by-stage UI goes.

---

## Part 8 — Tests

No containers, no model calls — the existing rule. Everything container-dependent is verified by
running it against a live deployment and reading the result.

| Test | Change |
|---|---|
| `test_workspace.py` | Set-shaped `stage`/`commit`; `verify` commits nothing to the branch (assert against a fake store). |
| `test_evidence.py` | `adopted` and `invalidated_by` gone; `onboarded` computed from addresses plus cubes. |
| `test_editor_tools.py` | The tools propose, write no ref, emit `proposal`, and read the overlay back. |
| `test_http.py` | `check`, `commit`, merge's `delete_after`; the deleted routes 404 or 405. |
| `test_deployment.py` | No `onboarded` property, no `reset_progress`. |
| `test_layering.py` | Unchanged — and it should stay unchanged, since no new package appears. |
| `test_content.py` | Whatever it asserts about copy on the deleted panels. |

---

## Part 9 — Docs

- `CLAUDE.md`: the editing subsystem section (staging is the browser's; `verify` vs `commit`),
  the deployment section (no rotate, no reset, no re-point), the pipeline section (onboarding
  owns it), and one new entry under *Things that will cost you an hour* about the browser being
  the only staging area.
- `CONTEXT.md`: **Ref** gains **branch** as the UI word instead of forbidding it; **Onboarded**
  gains the model.
- `docs/adr/0003-staging-is-the-browsers.md`: why the portal holds no staged edit, what that
  costs (two tabs disagree, a refresh loses accepted AI work), and why a shadow ref in the store
  and a table in `analyst.db` were both rejected.

---

## Part 10 — Risks, stated rather than discovered

1. **Browser-only staging means two tabs on one branch fight, and a crashed tab loses accepted
   AI work.** Accepted. The bar says so.
2. **`propose_model_file` has no implicit compile check** the way `write_model_file` had one via
   the commit. The prompt must push `validate_model` before the agent declares itself done, or it
   will confidently propose YAML that fails at Save.
3. **`preview` is a real ref and Cube compiles it like any other.** Harmless — nothing queries it
   — but it appears in the store's ref list while a Save is in flight, so the branch dropdown
   filters it out by name.
4. **Onboarding is now long.** `read`, then a table choice, then `cubes`, then one model call per
   table. The wizard has to keep narrating or it reads as hung.

---

## Part 11 — What was actually built, and where it deviates

Built 2026-08-22. Phases 1–6 are in; the record below is the diff between this plan and the tree,
kept because a plan that quietly disagrees with the code is worse than no plan.

**As specified, without deviation:** `stage`/`verify`/`commit` in `workspace.py` as one module and
`verify` as one function; the browser-only staging area; Save-the-whole-set; `POST /check` and
`POST /commit` replacing the per-path routes; the `proposal` event carrying a body and being
stored; the agent proposing into an overlay it reads back; `onboarded` computed in `evidence`;
`BuildModel` shared by the wizard's last step and Rebuild; CodeMirror with `unifiedMergeView` for
hunk review; `adopted`, follow-a-moved-deployment, reset-pipeline and rotate-secrets all deleted.
**Zero new Python modules**, as promised.

**Six deviations, each with its reason:**

1. **`GET /api/model` gained a `?ref=`.** Not in the plan, which
   said the Served-model tab was unchanged. Without it the tab shows main while you edit a branch,
   so the one question the editor exists to answer — did my view come out with the members I meant
   — could only be answered by merging first. Three lines each.
2. **`GET /files` ships no `kind`.** The plan had one per path for the tree. The prefix already is
   the kind, and the route filters by `workspace.resolve`, so the browser groups on the prefix and
   there is one rule instead of two.
3. **`GET /api/model/chats` deleted.** Follows from "fresh chat each session": the route had no
   caller left.
4. **`TablePicker` took `label`, `confirm` and `extra` instead of `discards`.** It had a
   stage-specific button label and a hardcoded warning about discarding later steps; both are now
   the caller's, which is what lets one picker serve onboarding and Rebuild.
5. **`Connect.tsx` deleted** along with its `.sources*` CSS, and the old `.editor-pane` /
   `.editor-assist` rules with it. The data page's "nothing connected yet" state cannot happen: the
   wizard owns the screen until setup finishes, and finishing requires a connection.
6. **`BuildModel` chains stages through the job, not one request.** The plan said "runs `cubes`,
   then `describe`/`views`" without saying how. Each `POST /pipeline/{stage}` answers as soon as the
   job exists and `steps.action` refuses a stage whose predecessor has not succeeded, so they
   cannot be fired together — a queue starts the next one when the last lands, which also means
   cancelling stops the rest.

**Cleaned up straight afterwards:** `evidence.stages()`, `Evidence.note()`, the
`stages`/`next_stage` fields and the browser's `StageState`/`STAGE_LABEL`/`STAGE_BLURB`/`summarise`
— all dead once the rail went. CodeMirror is lazy-loaded, so the main bundle is 468 kB rather than
912 with a 444 kB chunk that arrives when a file is first opened. The leftover `/cube/` directory
is deleted and `npm audit` is clean.

**Still shipped and read by nothing:** `cubes`, `views` and `steps` on the connection body. The
first two are one line each and harmless; `steps` is the only place a failed `describe`'s detail is
persisted, and deciding whether `save_step` should exist at all is a separate question from this
cleanup.

**Verified:** 567 tests pass, `make lint` clean, `make web-build` clean, `web.server` imports the
launcher's way. **Not verified:** anything requiring a live deployment — the `preview` ref round
trip, hunk review against a real proposal, and the wizard's fourth step end to end. Those need a
running store and Cube, per the repo's rule that container-dependent behaviour is checked by
running it rather than by mocking.
