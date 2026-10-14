# The query gate

The agent answers questions about the one connected database and **never writes SQL**. It emits
Cube JSON queries (`modelstore/schema.py` → `CubeQuery`, `extra="forbid"`), and every query passes
a four-tier gate in `modelstore/gate.py`.

| Tier | What | Where | Cost |
|---|---|---|---|
| 1 | Pydantic schema | `gate.parse` | free |
| 2 | Member existence, kind (measure/dimension/segment), operator vs type, WHERE-vs-HAVING mixing, granularity, cross-entity joinability | `gate.semantic_check` | free |
| 3 | `POST /refs/{ref}/sql` — Cube compiles, executes nothing | `gate.check` | one HTTP call |
| 4 | `POST /refs/{ref}/query` — execute with row and time caps | `gate.execute` / `gate.pages` | warehouse |

**Failures are text the agent can act on, never exceptions.** A tier-2 rejection carries the
nearest member names (`'orders.cnt' does not exist. Closest matches: orders.count`).

## Entry points

| Function | Tiers | Used by |
|---|---|---|
| `gate.run` | 1–4 | a chat turn, MCP `run_query` — one query, executed or explained |
| `gate.check` | 1–3 | dashboards (know a query is sound without running it), Flow `check_query` / `validate_flow` |
| `gate.execute` | 4 | after `check`. Takes the `GateResult`, so SQL and warnings survive rather than being recompiled |
| `gate.pages` | 4, paged | Flow `extract`. Reads `size` rows at a time by offset, up to `most` |

`check` always sets a limit: an absent `limit` becomes `max_rows`. A ceiling is not optional.

### Why offset paging is safe

`pages` appends every grouped time dimension and every dimension to the query's order, ascending,
after whatever order was asked for. There is no `ungrouped`, so no two rows share every dimension
value, and that order is total. Without it, ties on a measure could move a row between pages —
one page repeats it and the next skips it.

## Why not SQL

Tier 2 can check operators and granularities precisely *because* it inspects a `CubeQuery`, and
none of that survives parsing SQL. The expressiveness gap — no ratio or period delta in a
`CubeQuery` — is closed **downstream**, by post-processing rows in the sandbox, not by widening
the gate.

## How Cube is reached

Only through the model store, at `/refs/{ref}/{query,sql,meta}`. The store mints the JWT for each
ref and revision, so nothing on this side handles a Cube token. `deployment.store_url` is the only
address; `ModelStore.for_deployment` builds the session; `store.at(ref)` returns a `Bound` — a
store and one ref — which is what everything that queries takes. Chats, dashboards and Flow runs
are pinned to `main`.

The established exception pair is `(StoreError, httpx.RequestError)`: httpx transport errors do
not subclass `OSError`.

## Results

`tool_end` carries `rows[:MAX_POINTS]` and a `result_id`; the full rows are their own `results`
row. Column headers and charts are pure functions of (query, catalog, rows) — `shape.columns_of`
and `charts.chart_for`. A grouped time dimension comes back under two keys
(`sales.invoiced_at` and `sales.invoiced_at.month`), which is why headers are computed, never
`Object.keys(rows[0])`.
