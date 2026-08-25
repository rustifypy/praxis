# The semantic layer is an external deployment this repo only renders a file for

`services/semantic/` is gone. The model store and Cube are published images built and released
from their own repository, and what this repo keeps is a client (`modelstore/`) and the
compose file `core/deployenv.py` renders.

Nothing about *what the portal does* changed. What changed is who owns the thing on the other end
of `store_url`, and the consequences of that are not all obvious.

## Why

The store was written here because there was nothing to point at. Once a better one existed —
git worktrees, immutable per-revision snapshots, one Cube process serving every ref off a shared
volume — keeping a second implementation of the same idea meant maintaining the worse one forever.
Deleting it removed a service, its Dockerfile, its test suite, a custom Cube image and two Makefile
targets from a repository whose subject is the portal.

## What the portal gave up, and what it got

**Gave up: a version handshake.** The old store reported `contract` on `/health` and
`reachable.semantic` warned on a mismatch. The new one reports none, and inventing one would be
asserting agreement rather than confirming it — so the store leg checks no contract, `CONTRACT`
now means the sandbox's alone, and **the pinned image tag is the only compatibility control there
is**. `deployenv.MODELSTORE_IMAGE` is that pin. Nothing detects a mismatch for you: a newer store
with a changed route surfaces as a `StoreError` at the first call, not at startup.

**Gave up: bulk read and bulk write.** The store takes one file per request and commits
separately, and has no whole-model read. Both are hidden in `modelstore/client.py` — `files()`
fans a listing out over `asyncio.gather`, and `edit()` is deletes, then writes, then one commit.
That is the whole reason the client exists rather than callers speaking HTTP.

**Gave up: `/cube/{path}` and `X-Model-Ref`.** Cube is now at `/refs/{ref}/{query,sql,meta}`, so
the ref is the route and the JWT the store mints names a ref *and* a revision. The revision is
what makes hot reload work, and it is why `cube.py` keys the compiled-model cache on `<ref>@<rev>`.

**Gave up: a real merge.** Fast-forward only. See `docs/adr/0005`.

**Got: no image of ours to build.** Cube runs its stock image with `cube.py` supplied through
compose `configs`, so the hand-over needs no registry but Docker Hub.

**Got: the store's read token became meaningless.** Cube reads snapshots off a shared volume and
never calls the store back, so `store_read_token` was deleted from the row. That is a schema
change, and there is no migration mechanism — the `.db` file has to go.

## The one thing to keep an eye on

`deployenv.SEMANTIC` is now the *only* copy of that compose file here, with the store's own
`compose.yml` as its reference rather than its source. They can drift. That was already an
accepted trade when both existed; it is now the only mechanism, so the differences are
deliberate and worth stating: this portal renders **three** services, not four. Upstream ships a
dev-mode `cube-refresh` for warehouse introspection and pre-aggregation building. This portal
reads the warehouse through the production Cube — it needs foreign keys and `pg_stats`
cardinality that Cube's introspection does not carry — and builds no pre-aggregations. So the
rendered file has `CUBEJS_DEV_MODE=false` everywhere and no port that must never be exposed.

`deployenv.CUBE_TIMEOUT_S` is rendered into that file for the same reason: every Cube-bound cap on
this side has to sit under the store's, and reading it out of a sibling directory stopped being
possible. `test_timeouts` holds the two in order.
