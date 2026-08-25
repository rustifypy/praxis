# Adopting `cube-model-store`, dropping `services/semantic/`

Scoping note. Two changes at once, and the second one is the bigger:

1. The semantic layer becomes an **external deployment**, built and published from
   `cube-model-store`. This repository keeps a client and a rendered hand-over.
   `services/semantic/` is deleted.
2. The portal keeps a **local working copy of a ref's model files**. The editor reads and writes
   that copy. A local save is a plain file write. Commit and push happen only at the deployment,
   over its REST API. **The deployment stays the source of truth.**

No git on this side — no repository, no subprocess, no history, no `origin` remote. "Branch",
"commit", "merge" are things the portal *asks the store to do* over HTTP. The local copy is a
working copy of a ref, re-derivable at any time by reading that ref's files back.

That last point is not a preference, it is forced: the store has **no git transport**. No
`upload-pack`, no `receive-pack`, no http-backend, and its design doc locks it in — *"ModelStore is
consumed over its HTTP API only. That is the entire surface, now and later."* A literal `git push`
would also reopen its risk 4: the YAML-only allowlist lives in the REST layer, so `receive-pack`
would accept a `.js` model file, which Cube compiles in the process holding the warehouse
credentials.

Read against `/Users/sakshamjain/Documents/personal/cube-model-store` at `src/modelstore/`, not
against its README — several findings below are only visible in the source.

## What the local copy buys

It is worth being explicit, because this reverses two documented decisions and the payoff is what
justifies that.

- **A save costs nothing.** Today every write reaches the store. In the new store every write bumps
  a rev, byte-copies a snapshot, and invalidates Cube's compiled-model cache. Local saves take all
  of that off the keystroke path: a 13-file rebuild becomes one publish instead of 13 recompiles.
- **Reads stop being N+1.** The new store has no bulk-read endpoint — only `tree` plus one GET per
  file. The editor, the file tree and the editor agent's tools all read local disk instead.
- **The rebase problem dissolves.** See "Publishing when main has moved" below. This was the one
  item I was going to ask you to decide; the local copy answers it.
- **The browser stops carrying the model.** ADR 0003 put unsaved edits in the tab because there was
  nowhere honest to put them. With a working copy there is, and all three of that ADR's
  consequences invert — a reload no longer loses work, two tabs no longer fight, and the server
  knows what the model looks like, so `EditContext.staged` and the staged-set-in-the-message-body
  mechanism can both go away.

The concern, stated once: this repository deleted a local model directory before, and ADR 0003
rejected a local staging copy on the grounds that it becomes a second source of truth. What makes
this different is that **no history lives locally**. The store owns every commit, every branch and
every merge; the local copy holds only uncommitted file content for one ref, and its staleness is
detectable because the store hands out a `rev` per ref. That is a working copy, not a second
truth. The ADR needs superseding rather than contradicting, and the superseding note should say
exactly that.

## Shape

```
$DATA_DIR/drafts/<ref>/model/cubes/*.yml      the working copy
$DATA_DIR/drafts/<ref>/model/views/*.yml
$DATA_DIR/drafts/<ref>/origin.json            {ref, rev, sha, synced_at}
```

`origin.json` is what makes staleness answerable: `GET /refs/{ref}` is one cheap call returning
`rev` and `sha`, so "the store moved under this draft" is a comparison, not a guess. It sits beside
the model rather than inside it, and there is deliberately **no table** — the same rule that keeps
an uploaded CSV out of the database applies here, a row would only ever be a stale copy of `ls`.

`main` gets a draft dir too, read-only. `stage` checks cubes append-only against main on every
call, and without a local copy of main that is N+1 per keystroke.

Four operations, and the words matter because `CONTEXT.md` has to carry them:

| word | what it does | reaches the store? |
|---|---|---|
| **save** | write one file into the draft | no |
| **discard** | drop a file, or the whole draft, back to `origin.json`'s content | no |
| **verify** | would Cube accept this draft? | yes — throwaway `preview` ref |
| **publish** | commit the draft onto the ref, and merge | yes — bulk write, commit, merge |

`verify` still has to reach the store, and that is not fixable: **Cube compiles only what is in the
store.** It writes the draft set to a `preview` ref, asks Cube, deletes the ref — unchanged from
today, except the set now comes off disk instead of out of a request body. Because verify is a
button and not every save, the per-verify `worktree add`/`remove` the new store does is acceptable.

## Publishing when main has moved

Merge in the new store is **fast-forward only**; once main moves, a ref must rebase first. Main is
*generated* here — re-running the pipeline rewrites every `cubes/*.yml` — so a store-side rebase of
an edit branch onto a regenerated main would conflict on files nobody touched.

The draft is the intent, and it holds complete file content, so this never needs conflict
resolution:

1. `POST /refs/{ref}/rebase`. If it succeeds, history is preserved; publish on top.
2. If it 409s on conflict, delete the ref, recreate it from current main, and publish the draft.
   Ours wins by construction.

`edits.check` against the *new* main still enforces cubes-append-only, so the guard that matters is
untouched. The cost of the fallback is the ref's intermediate store-side commits — recommend taking
it, and saying so in the response, since every publish is already a full-set write and those
intermediate commits are an audit trail rather than a merge base.

## What maps, and what does not

| what the portal needs | in the new store | verdict |
|---|---|---|
| list / create / delete refs | `GET/POST /refs`, `DELETE /refs/{ref}` | ✓ list is a bare array keyed `ref`, not `name` |
| detect a moved ref | `rev` + `sha` on `GET /refs/{ref}` | ✓ int, not a sha |
| read a ref's whole model (sync) | `tree` + one GET per file | ✗ **N+1** — ask 2 |
| publish a draft | one PUT per file, DELETEs, then `POST /commit` | ✗ **N revs, N snapshots** — ask 1 |
| merge onto main | `POST /refs/{ref}/merge` | ✓ FF-only; see above |
| diff against main | `GET /refs/{ref}/diff` | ✓ `files: [str]`, not `[{status, path}]` |
| `store.log` | — | unused here; delete it |
| Cube `/v1/load` | `POST /refs/{ref}/query` | ✓ same `{"query": …}` body, verbatim response |
| Cube `/v1/sql` (gate tier 3) | `POST /refs/{ref}/sql` | ✓ |
| Cube `/v1/meta` | `GET /refs/{ref}/meta` | ✓ |
| Cube `/readyz` through the store | — | ✗ no passthrough — ask 4 |
| store identity + contract | `GET /healthz` → `{"status": "ok"}` | ✗ no `ok`, no `contract` — ask 3 |
| refusal body | `{"error": …}` | ✗ our client reads `detail` |
| ref names containing `/` | `_REF_RE` excludes `/` | ✗ refused |
| a `read` probe query up to 110 s | `cube_timeout_seconds: 60.0` | ✗ cap is below our timeout |

Three facts from the source that shaped the above:

- **A snapshot is materialised from the worktree on every write**, so Cube serves *uncommitted*
  content. Dirty constrains only git ops. This is why publish can write-then-commit and still have
  verify see the bytes.
- **`create_ref` branches from the commit**, so uncommitted store-side state is never inherited —
  which is fine here, because the portal never leaves a store ref dirty.
- **`main` is not writable through the file API**, and merge / rebase / delete refuse it. Matches
  `workspace.editable` and `publish.install` (always called with `ref=MAIN`), so merge-into-prod-only
  is sufficient.

## Upstream asks — `cube-model-store`

Reordered by what the local copy makes urgent.

1. **Bulk write + commit: `PUT /refs/{ref}/files` → `{write: {}, delete: [], message, replace}`,
   one rev, one snapshot, one commit.** This is now *the* publish primitive. Per-file, a 13-file
   publish is 13 revs, 13 byte-copied snapshots and 13 compiled-model cache misses, then a separate
   commit call. `replace: true` is what makes a locally deleted file actually go, and what
   `publish.install` needs for whole-model-in-one-commit.
2. **Bulk read: `GET /refs/{ref}/files` → `{ref, rev, files: {path: text}}`.** Now a *sync*
   primitive rather than a per-page-load one — once per draft refresh, plus main whenever its rev
   moves. Downgraded from blocking to strongly wanted.
3. **`/healthz` → `{"status": "ok", "ok": true, "contract": N}`.** The check is "did the *model
   store* answer, or something else on that port", which needs a positive marker; the contract
   integer is how a deployment rendered against an older portal gets warned rather than refused.
4. **A Cube readiness passthrough** — `GET /refs/{ref}/readyz`. Otherwise page-load health has to
   use `/meta`, which compiles the model.

Also: pass `MODELSTORE_CUBE_TIMEOUT_SECONDS` through in the compose template, and start sending
`X-Actor` on publish — it already exists upstream and is exactly how a person's publish gets
distinguished from the editor agent's in the store's history.

## Still to decide

1. **Names.** `workspace` is taken, and `worktree` would imply git. I suggest the package is
   `drafts/` and the verbs are save / discard / verify / publish, with `workspace.py` keeping its
   role as the one door. Needs to land in `CONTEXT.md` before any of it is written.
2. **Drop `cube-refresh` from the rendered hand-over** — recommend yes. Upstream ships a dev-mode
   Cube on loopback for `/introspect`, `/scaffold` and pre-agg building. We want none of them:
   `read.py` deliberately avoids `/playground/db-schema` because it needs foreign keys and
   `pg_stats` cardinality that Cube's introspection does not carry and `modelgen` feeds both to the
   LLM; `/scaffold` renders YAML, which is `modelgen`'s job; nothing here builds pre-aggregations.
   So we render three containers — `modelstore`, `cube-api`, `cubestore` — with
   `CUBEJS_DEV_MODE=false` everywhere and no port that must never be exposed.
3. **Refuse `pre_aggregations:` in `edits.check`.** Upstream's own open problem: a branch
   inheriting main's pre-agg block *errors* on any matching query rather than falling back to raw
   SQL. This portal queries non-main refs constantly (`candidate`, `preview`, `probe`). Nothing
   writes such a block today, but the editor agent could. One refusal, and a class of "previews
   mysteriously fail" becomes a message.
4. **Pin the image.** Render `skshmjn/modelstore:<version>`, not `latest` + `pull_policy: always`.
   Contract compatibility is the portal's problem, and the pin should be bumped with `CONTRACT`.
5. **Concurrent drafts.** One draft dir per ref means two tabs on one ref now *share* state instead
   of fighting, which is an improvement. Two chats editing two refs is two dirs, fine. Worth
   confirming nothing needs a per-session draft.

## Work, by phase

**Phase 0 — upstream.** Asks 1–4, publish a pinned tag.

**Phase 1 — the two clients.** `modelstore/client.py`: `{"error"}` not `{"detail"}`, bare-array
`GET /refs` keyed `ref`, `rev` in place of `sha`, bulk write plus commit, `diff.files` mapped to
`changed`, `POST /refs` sends `from`, `DELETE` answers 204, drop `log`/`Commit`.
`cube/client.py`: three concrete routes instead of a proxy prefix plus `X-Model-Ref`; delete
`CUBE_PREFIX`, `settings.cube_base_path`, `REF_HEADER`. Drop `PROBE_QUERY_TIMEOUT_S` below the
store's 60 s cap. Signatures held here means `modelgen`, the gate and the agent paths do not move.

**Phase 2 — `drafts/`.** New tier-1 package: layout, save, discard, sync-from-ref, staleness
against `origin.json`. `test_layering.py` must be given the tier or the suite fails. Nothing
consumes it yet.

**Phase 3 — reachability and the row.** `reachable.semantic`/`querying` re-aimed at `/healthz` plus
the readiness passthrough. `ref_token` 409s when `rev == 0`, a real new condition — *deployed, Cube
up, no model yet* — which belongs in `serving.state`, not reported as an outage. Drop
`store_read_token` from `Deployment` and `db.deployment`: Cube reads snapshots off the shared volume
and never calls back, so the read token has no purpose. **No migration mechanism — the `.db` file
has to be deleted.** Bump `CONTRACT`.

**Phase 4 — the hand-over.** `deployenv.SEMANTIC` rewritten: three services, `cube.py` as an inline
compose `config` (so the custom `analyst-cube` image goes away and the JDK case becomes stock
`cubejs/cube:<v>-jdk`), `CUBESTORE_IMAGE` for `CUBESTORE_TAG`, credentials rendered into the
`x-cube-env` anchor. The `$`-doubling and JSON-quoting rules carry over unchanged and still matter.
No `.env` — upstream's `${VAR:?required}` guards are replaced by inlined values, which is the point
of the download button. Needs Compose v2.23+ for inline `config` content; say so in the file's own
comments. Then delete `services/semantic/` and the `store-test` / `store-check` Makefile targets.

**Phase 5 — repoint the editor at `drafts/`.** `workspace.stage`/`verify` read the draft;
`workspace.commit` becomes `publish` with the rebase-or-recreate path; `agent/tools/editor.py` reads
the draft and `EditContext.staged` is deleted; `web/model.py` grows discard / sync / status and
loses the staged set from its chat bodies; `ModelEditor.tsx` and `FileTree.tsx` read dirty state off
the server instead of deriving it from a local buffer. Tighten `workspace.validate_ref` to reject
`/`. This is the largest phase and the only one that touches the browser.

**Phase 6 — docs.** `CLAUDE.md`'s tier table, the editing and deployment sections, and the
Cube-behaviours list all describe the old store and the no-local-directory rule. `CONTEXT.md` gains
*draft*, *rev*, *prod ref*, *publish*; loses *read token*. Three ADRs: the semantic layer is an
external deployment; a working copy is not a second source of truth (superseding 0003); publishing
prefers rebase and falls back to recreate.

## Risks

- **The draft dir is unbacked-up work.** No history lives there, so the exposure is bounded to
  uncommitted edits — but it is real, and `DATA_DIR` is already the thing a scratch dir scopes.
- **Staleness is now a UX surface that does not exist yet.** "The store moved under this draft"
  needs a state, a message and a resolution path in the editor. Detectable cheaply; still has to be
  designed.
- **Two truths for what "the model" means during an edit.** Mitigated by `origin.json` and by the
  fact that only the editor path reads drafts — `modelgen` and every dashboard still read main
  from the store, unchanged. Keep that separation explicit or the mitigation erodes.
- **Upstream drift.** `deployenv.SEMANTIC` stops being "the `services/` file with `build:` dropped"
  and becomes the only copy here, with upstream's `compose.yml` as reference. Already an accepted
  trade; now it is the only mechanism, so the ADR should say so.
