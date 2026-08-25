"""Assembling a board for the page, and the only Refresh.

`refresh` was `dashboards/refresh.py` and is here now because refresh is the page's, not the
agent's: a tile the agent just added has no data until someone presses the button. Everything
else here is what `routes.py` used to do inline.

`checks` and `refresh` compile the same tiles through the same `_compile_all`, so the banner on
arrival and the verdict on the button cannot disagree about what a legal tile is.
"""

import asyncio
from dataclasses import asdict, dataclass, field

from ...core import boards
from ...core.boards import Dashboard, Tile
from ...core.config import settings
from ...modelstore import charts, gate
from ...modelstore.catalog import Catalog
from ...modelstore.client import Bound, StoreError
from ...modelstore.shape import Column, columns_of
from . import delta
from .delta import Delta

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
    board = await boards.get(dashboard_id)
    if board is None:
        return (
            None,
            [],
            RunOutcome(ok=False, status="failed", error=f"no dashboard {dashboard_id!r}"),
        )
    return board, await boards.tiles(dashboard_id), None


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
    run = await boards.start_run(dashboard_id)
    checks = await _compile_all(payloads, catalog, client, max_rows)
    outcomes = [_outcome(tile, checked) for tile, checked in zip(live_tiles, checks, strict=True)]

    # The atomic part. Every tile compiles or none executes.
    if not all(outcome.ok for outcome in outcomes):
        errors = [
            {"tile_id": outcome.tile_id, "title": outcome.title, "errors": outcome.errors}
            for outcome in outcomes
            if not outcome.ok
        ]
        await boards.finish_run(run.id, "refused", errors=errors)
        return RunOutcome(ok=False, status="refused", run_id=run.id, tiles=outcomes)

    await boards.set_run_status(run.id, "running")
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
        outcome.degraded = charts.outgrown(tile.chart, columns, rows)
        outcome.row_count = len(rows)
        # Read before this run's own result is saved, so the newest entry is genuinely the
        # previous refresh.
        earlier = await boards.tile_history(tile.id, limit=1)
        await boards.save_tile_result(
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
        await boards.finish_run(run.id, "failed", errors=failures)
        return RunOutcome(ok=False, status="failed", run_id=run.id, tiles=outcomes)

    # Only on success: pruning a refused run would drop the rows a tile is still showing.
    await boards.prune(dashboard_id)
    await boards.finish_run(run.id, "ok")
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


async def read(dashboard_id: str, cube: Bound) -> dict | None:
    # None when there is no such board — which status code that is belongs to the route.
    board = await boards.get(dashboard_id)
    if board is None:
        return None

    tiles = await boards.tiles(dashboard_id)
    current = await boards.layout(dashboard_id)
    spec = current["spec"] if current else boards.default_layout(tiles)

    # The last two runs that produced data, in two queries rather than one per tile. Comparing
    # ok-run to ok-run also fixes what per-tile history got wrong: a `failed` run stores the
    # tiles that did execute, so its rows can sit on top of the run being rendered and the
    # tile would diff against itself.
    recent = await boards.runs(dashboard_id, limit=2, status="ok")
    last = recent[0] if recent else None
    results: dict[str, dict] = {}
    if last is not None:
        stored = {result.tile_id: result for result in await boards.results_for_run(last.id)}
        earlier = (
            {result.tile_id: result for result in await boards.results_for_run(recent[1].id)}
            if len(recent) > 1
            else {}
        )
        for tile in tiles:
            record = stored.get(tile.id)
            if record is None:
                continue
            columns = [Column(**raw) for raw in record.columns]
            changes, since = delta.deltas(record.rows, earlier.get(tile.id), columns)
            results[tile.id] = {
                # Recomputed rather than stored: the verdict depends on the rows and
                # `tile_results` cannot gain a column, and without it a reload would draw the
                # chart `outgrown` rejected at refresh time.
                "degraded": (
                    charts.outgrown(tile.chart, columns, record.rows) if record.rows else ""
                ),
                "columns": record.columns,
                "rows": record.rows or [],
                "row_count": record.row_count,
                "resolved_ranges": record.resolved_ranges,
                "duration_ms": record.duration_ms,
                # Not `rows.length`: a run past the retention depth keeps its count.
                "pruned": record.rows is None,
                "deltas": [asdict(change) for change in changes],
                "compared_at": since,
            }

    return {
        **board.__dict__,
        "tiles": [
            {
                "id": tile.id,
                "title": tile.title,
                "query": tile.query,
                "chart": tile.chart,
            }
            for tile in tiles
        ],
        "layout": spec,
        # Shipped rather than mirrored in the browser: how wide a board may be is a layout
        # rule, and a second copy of it is one the column picker can drift from.
        "max_columns": boards.MAX_COLUMNS,
        "layout_version": current["version"] if current else None,
        "history": await boards.layout_history(dashboard_id),
        "run": (
            None
            if last is None
            else {
                "id": last.id,
                "status": last.status,
                "started_at": last.started_at,
                "finished_at": last.finished_at,
            }
        ),
        "results": results,
        "checks": await checks(dashboard_id, cube),
    }


async def checks(dashboard_id: str, cube: Bound) -> dict:
    # Compiles every tile without executing it, and never fails the page for trying: a member
    # dropped by a model rebuild becomes a banner on arrival.
    try:
        catalog = await cube.catalog()
        outcome = await validate(dashboard_id, catalog, cube, settings.max_rows)
        return {
            "available": True,
            "reason": "",
            "broken": [
                {
                    "tile_id": outcome_tile.tile_id,
                    "title": outcome_tile.title,
                    "errors": outcome_tile.errors,
                }
                for outcome_tile in outcome.tiles
                if not outcome_tile.ok
            ],
        }
    except StoreError as exc:
        # The session wraps transport failures, so an unreachable Cube arrives here rather
        # than as a ConnectError — which once escaped an `except OSError` and 500'd the page.
        return {"available": False, "reason": str(exc), "broken": []}


async def listing() -> list[dict]:
    """Every board with its tile count and when it last ran, for the boards list."""
    out = []
    for board in await boards.list_dashboards():
        tiles = await boards.tiles(board.id)
        last = await boards.latest_run(board.id)
        out.append(
            {
                **board.__dict__,
                "tiles": len(tiles),
                "refreshed_at": last.started_at if last else None,
            }
        )
    return out


async def run(dashboard_id: str, cube: Bound) -> dict:
    """One refresh, as the button reports it. `StoreError` is left to the route: Cube being
    unreachable is a 502 and not a failed run."""
    outcome = await refresh(dashboard_id, await cube.catalog(), cube, settings.max_rows)
    return {
        "ok": outcome.ok,
        "status": outcome.status,
        "run_id": outcome.run_id,
        "error": outcome.error,
        "tiles": [
            {
                "tile_id": tile.tile_id,
                "title": tile.title,
                "ok": tile.ok,
                "errors": tile.errors,
                "warnings": tile.warnings,
                "row_count": tile.row_count,
                "degraded": tile.degraded,
            }
            for tile in outcome.tiles
        ],
    }
