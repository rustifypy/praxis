"""`validate` is tiers 1-3 and touches no data; `refresh` adds execution, and nothing executes
until every tile has compiled. `refused` did not compile, `failed` compiled then broke.
"""

import asyncio
from dataclasses import asdict, dataclass, field

from ..cube import gate
from ..cube.catalog import Catalog
from ..cube.client import CubeClient
from ..cube.shape import columns_of
from ..sources.store import SourceStore
from . import delta, insights
from . import tiles as tilecheck
from .delta import Delta
from .store import Dashboard, DashboardStore, Tile

CONCURRENCY = 4
"""Tiles at once. A cap on the Cube container, which shares a Cube Store with every source."""


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
    error: str = ""
    """Set when the refresh never reached the tiles — a wrong active source, or no tiles."""
    insight: str = ""


async def active_source_name() -> str:
    """The name of the source `CUBE_URL` points at, or "" if it points outside the registry."""
    active = next((s for s in await SourceStore().all() if s.is_active), None)
    return active.name if active else ""


async def source_mismatch(source_name: str) -> str | None:
    """Why this dashboard's queries must not run right now, or None. The only thing between a
    saved query and a number from the wrong warehouse.
    """
    active = await active_source_name()
    if not active:
        return (
            "No registered source is active, so there is nothing to run these queries "
            "against. Set CUBE_URL to a source in the registry and restart."
        )
    if active != source_name:
        return (
            f"This dashboard was built on '{source_name}' and the portal is querying "
            f"'{active}'. Refreshing would answer its questions from the wrong database. "
            f"Set CUBE_URL to '{source_name}' and restart to refresh it."
        )
    return None


async def _open(
    dashboard_id: str, store: DashboardStore
) -> tuple[Dashboard | None, list[Tile], RunOutcome | None]:
    """The board, its live tiles, and the outcome to return instead if it cannot run. Shared so
    the source check cannot apply to one entry point and not the other."""
    board = await store.get(dashboard_id)
    if board is None:
        return (
            None,
            [],
            RunOutcome(ok=False, status="failed", error=f"no dashboard {dashboard_id!r}"),
        )
    mismatch = await source_mismatch(board.source_name)
    if mismatch:
        return board, [], RunOutcome(ok=False, status="refused", error=mismatch)
    return board, await store.tiles(dashboard_id), None


async def validate(
    dashboard_id: str,
    store: DashboardStore,
    catalog: Catalog,
    client: CubeClient,
    max_rows: int,
) -> RunOutcome:
    """Tiers 1-3 for every tile. Executes nothing and records no run."""
    _, live, stop = await _open(dashboard_id, store)
    if stop is not None:
        return stop
    if not live:
        return RunOutcome(ok=True, status="ok")

    checks = await _compile_all(live, catalog, client, max_rows)
    outcomes = [_outcome(tile, checked) for tile, checked in zip(live, checks, strict=True)]
    ok = all(o.ok for o in outcomes)
    return RunOutcome(ok=ok, status="ok" if ok else "refused", tiles=outcomes)


async def refresh(
    dashboard_id: str,
    store: DashboardStore,
    catalog: Catalog,
    client: CubeClient,
    max_rows: int,
) -> RunOutcome:
    """Re-execute every tile's stored query verbatim and append the results as one run."""
    board, live, stop = await _open(dashboard_id, store)
    if stop is not None:
        return stop
    if not live:
        return RunOutcome(ok=False, status="refused", error="this dashboard has no tiles")

    run = await store.start_run(dashboard_id)
    checks = await _compile_all(live, catalog, client, max_rows)
    outcomes = [_outcome(tile, checked) for tile, checked in zip(live, checks, strict=True)]

    # The atomic part. Every tile compiles or none executes.
    if not all(o.ok for o in outcomes):
        errors = [
            {"tile_id": o.tile_id, "title": o.title, "errors": o.errors}
            for o in outcomes
            if not o.ok
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
    briefing: list[tuple] = []
    for tile, outcome, result in zip(live, outcomes, loaded, strict=True):
        if not result.ok:
            outcome.ok = False
            outcome.errors = result.errors
            failures.append({"tile_id": tile.id, "title": tile.title, "errors": result.errors})
            continue
        rows = result.rows or []
        columns = columns_of(tile.query, catalog)
        # Re-checked against the rows that just arrived, not the ones it was authored on.
        outcome.degraded = tilecheck.outgrown(tile.chart, columns, rows)
        outcome.row_count = len(rows)
        # Read before this run's own result is saved, so the newest entry is genuinely the
        # previous refresh.
        earlier = await store.tile_history(tile.id, limit=1)
        await store.save_tile_result(
            run.id,
            tile.id,
            [asdict(c) for c in columns],
            rows,
            resolved_ranges=result.resolved_ranges,
            duration_ms=result.duration_ms,
        )
        changes, since = delta.deltas(rows, earlier[0] if earlier else None, columns)
        outcome.deltas = changes
        briefing.append((tile.title, columns, rows, changes, since))

    if failures:
        await store.finish_run(run.id, "failed", errors=failures)
        return RunOutcome(ok=False, status="failed", run_id=run.id, tiles=outcomes)

    # After the rows are stored, so a model call that hangs cannot cost the refresh its data.
    note = await insights.summarise(board.title, briefing)
    # Only on success: pruning a refused run would drop the rows a tile is still showing.
    await store.prune(dashboard_id)
    await store.finish_run(run.id, "ok", insight=note)
    return RunOutcome(ok=True, status="ok", run_id=run.id, tiles=outcomes, insight=note)


async def _compile_all(
    live: list[Tile], catalog: Catalog, client: CubeClient, max_rows: int
) -> list[gate.GateResult]:
    limit = asyncio.Semaphore(CONCURRENCY)

    async def one(tile: Tile) -> gate.GateResult:
        async with limit:
            return await gate.check(tile.query, catalog, client, max_rows)

    return list(await asyncio.gather(*(one(tile) for tile in live)))


def _outcome(tile: Tile, checked: gate.GateResult) -> TileOutcome:
    return TileOutcome(
        tile_id=tile.id,
        title=tile.title,
        ok=checked.ok,
        errors=checked.errors,
        warnings=checked.warnings,
    )
