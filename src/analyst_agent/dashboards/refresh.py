import asyncio
from dataclasses import asdict, dataclass, field

from ..modelstore import gate
from ..modelstore.catalog import Catalog
from ..modelstore.client import Bound
from ..modelstore.shape import columns_of
from . import delta, store
from . import tiles as tilecheck
from .delta import Delta
from .store import Dashboard, Tile

# Tiles at once — a cap on the Cube container, which shares a Cube Store with the workers.
CONCURRENCY = 4


@dataclass
class TileOutcome:
    tile_id: str
    title: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_count: int = 0
    degraded: str = ""
    deltas: list[Delta] = field(default_factory=list)


@dataclass
class RunOutcome:
    ok: bool
    status: str
    run_id: str = ""
    tiles: list[TileOutcome] = field(default_factory=list)
    # Set when the refresh never reached the tiles: no dashboard, or no tiles.
    error: str = ""


async def _open(dashboard_id: str) -> tuple[Dashboard | None, list[Tile], RunOutcome | None]:
    # Shared, so the page-open check and the refresh cannot compile different queries.
    board = await store.get(dashboard_id)
    if board is None:
        return (
            None,
            [],
            RunOutcome(ok=False, status="failed", error=f"no dashboard {dashboard_id!r}"),
        )
    return board, await store.tiles(dashboard_id), None


async def validate(
    dashboard_id: str,
    catalog: Catalog,
    client: Bound,
    max_rows: int,
) -> RunOutcome:
    _, live_tiles, refusal = await _open(dashboard_id)
    if refusal is not None:
        return refusal
    if not live_tiles:
        return RunOutcome(ok=True, status="ok")

    payloads = [tile.query for tile in live_tiles]
    checks = await _compile_all(payloads, catalog, client, max_rows)
    outcomes = [_outcome(tile, checked) for tile, checked in zip(live_tiles, checks, strict=True)]
    ok = all(outcome.ok for outcome in outcomes)
    return RunOutcome(ok=ok, status="ok" if ok else "refused", tiles=outcomes)


async def refresh(
    dashboard_id: str,
    catalog: Catalog,
    client: Bound,
    max_rows: int,
) -> RunOutcome:
    board, live_tiles, refusal = await _open(dashboard_id)
    if refusal is not None:
        return refusal
    if not live_tiles:
        return RunOutcome(ok=False, status="refused", error="this dashboard has no tiles")

    payloads = [tile.query for tile in live_tiles]
    run = await store.start_run(dashboard_id)
    checks = await _compile_all(payloads, catalog, client, max_rows)
    outcomes = [_outcome(tile, checked) for tile, checked in zip(live_tiles, checks, strict=True)]

    # The atomic part. Every tile compiles or none executes.
    if not all(outcome.ok for outcome in outcomes):
        errors = [
            {"tile_id": outcome.tile_id, "title": outcome.title, "errors": outcome.errors}
            for outcome in outcomes
            if not outcome.ok
        ]
        await store.finish_run(run.id, "refused", errors=errors)
        return RunOutcome(ok=False, status="refused", run_id=run.id, tiles=outcomes)

    await store.set_run_status(run.id, "running")
    limit = asyncio.Semaphore(CONCURRENCY)

    async def load(checked: gate.GateResult) -> gate.GateResult:
        async with limit:
            return await gate.execute(checked, client)

    loaded = await asyncio.gather(*(load(checked) for checked in checks))

    failures: list[dict] = []
    for tile, payload, outcome, result in zip(live_tiles, payloads, outcomes, loaded, strict=True):
        if not result.ok:
            outcome.ok = False
            outcome.errors = result.errors
            failures.append({"tile_id": tile.id, "title": tile.title, "errors": result.errors})
            continue
        rows = result.rows or []
        # The payload that ran, not the stored query — a header taken from a query that was
        # not executed is the kind of drift that stays invisible until it is wrong.
        columns = columns_of(payload, catalog)
        # Re-checked against the rows that just arrived, not the ones it was authored on.
        outcome.degraded = tilecheck.outgrown(tile.chart, columns, rows)
        outcome.row_count = len(rows)
        # Read before this run's own result is saved, so the newest entry is genuinely the
        # previous refresh.
        earlier = await store.tile_history(tile.id, limit=1)
        await store.save_tile_result(
            run.id,
            tile.id,
            [asdict(column) for column in columns],
            rows,
            resolved_ranges=result.resolved_ranges,
            duration_ms=result.duration_ms,
        )
        changes, since = delta.deltas(rows, earlier[0] if earlier else None, columns)
        outcome.deltas = changes

    if failures:
        await store.finish_run(run.id, "failed", errors=failures)
        return RunOutcome(ok=False, status="failed", run_id=run.id, tiles=outcomes)

    # Only on success: pruning a refused run would drop the rows a tile is still showing.
    await store.prune(dashboard_id)
    await store.finish_run(run.id, "ok")
    return RunOutcome(ok=True, status="ok", run_id=run.id, tiles=outcomes)


async def _compile_all(
    payloads: list[dict], catalog: Catalog, client: Bound, max_rows: int
) -> list[gate.GateResult]:
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(payload: dict) -> gate.GateResult:
        async with limit:
            return await gate.check(payload, catalog, client, max_rows)

    return list(await asyncio.gather(*(one(payload) for payload in payloads)))


def _outcome(tile: Tile, checked: gate.GateResult) -> TileOutcome:
    return TileOutcome(
        tile_id=tile.id,
        title=tile.title,
        ok=checked.ok,
        errors=checked.errors,
        warnings=checked.warnings,
    )
