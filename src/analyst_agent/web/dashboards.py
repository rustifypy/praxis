"""Boards: the grid, its tiles, its layout history, and the only Refresh.

Refresh is here and nowhere else, deliberately: the agent authors and arranges, the page runs.
An agent that could trigger one would turn "tidy up this dashboard" into two dozen warehouse
queries — so a tile it just added has no data until someone presses the button.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.config import settings
from ..dashboards import delta, store
from ..dashboards import layout as layouts
from ..dashboards import refresh as runner
from ..dashboards import tiles as tilecheck
from ..modelstore.client import Bound, StoreError
from ..modelstore.shape import Column
from .support import state

router = APIRouter(prefix="/api/dashboards")


class NewDashboard(BaseModel):
    title: str = "New dashboard"


class Rename(BaseModel):
    title: str


@router.get("")
async def list_dashboards() -> list[dict]:
    out = []
    for board in await store.list_dashboards():
        tiles = await store.tiles(board.id)
        last = await store.latest_run(board.id)
        out.append(
            {
                **board.__dict__,
                "tiles": len(tiles),
                "refreshed_at": last.started_at if last else None,
            }
        )
    return out


@router.post("")
async def create_dashboard(body: NewDashboard) -> dict:
    board = await store.create(body.title)
    return board.__dict__


@router.get("/{dashboard_id}")
async def read_dashboard(dashboard_id: str, request: Request) -> dict:
    board = await store.get(dashboard_id)
    if board is None:
        raise HTTPException(404, f"no dashboard with id {dashboard_id!r}")

    tiles = await store.tiles(dashboard_id)
    current = await store.layout(dashboard_id)
    spec = current["spec"] if current else layouts.default_layout(tiles)

    # The last two runs that produced data, in two queries rather than one per tile. Comparing
    # ok-run to ok-run also fixes what per-tile history got wrong: a `failed` run stores the
    # tiles that did execute, so its rows can sit on top of the run being rendered and the
    # tile would diff against itself.
    recent = await store.runs(dashboard_id, limit=2, status="ok")
    last = recent[0] if recent else None
    results: dict[str, dict] = {}
    if last is not None:
        stored = {result.tile_id: result for result in await store.results_for_run(last.id)}
        earlier = (
            {result.tile_id: result for result in await store.results_for_run(recent[1].id)}
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
                    tilecheck.outgrown(tile.chart, columns, record.rows) if record.rows else ""
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
        "max_columns": layouts.MAX_COLUMNS,
        "layout_version": current["version"] if current else None,
        "history": await store.layout_history(dashboard_id),
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
        "checks": await _checks(dashboard_id, await state.cube(request.app)),
    }


async def _checks(dashboard_id: str, cube: Bound) -> dict:
    # Compiles every tile without executing it, and never fails the page for trying: a member
    # dropped by a model rebuild becomes a banner on arrival.
    try:
        catalog = await cube.catalog()
        outcome = await runner.validate(dashboard_id, catalog, cube, settings.max_rows)
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


@router.patch("/{dashboard_id}")
async def rename_dashboard(dashboard_id: str, body: Rename) -> dict:
    await store.rename(dashboard_id, body.title)
    return {"ok": True}


@router.delete("/{dashboard_id}")
async def delete_dashboard(dashboard_id: str) -> dict:
    await store.delete_dashboard(dashboard_id)
    return {"ok": True}


@router.post("/{dashboard_id}/refresh")
async def refresh_dashboard(dashboard_id: str, request: Request) -> dict:
    # Atomic: every tile compiles, or none of them run.
    try:
        cube = await state.cube(request.app)
        catalog = await cube.catalog()
        outcome = await runner.refresh(dashboard_id, catalog, cube, settings.max_rows)
        return {
            "ok": outcome.ok,
            "status": outcome.status,
            "run_id": outcome.run_id,
            "error": outcome.error,
            "tiles": [
                {
                    "tile_id": outcome_tile.tile_id,
                    "title": outcome_tile.title,
                    "ok": outcome_tile.ok,
                    "errors": outcome_tile.errors,
                    "warnings": outcome_tile.warnings,
                    "row_count": outcome_tile.row_count,
                    "degraded": outcome_tile.degraded,
                }
                for outcome_tile in outcome.tiles
            ],
        }
    except StoreError as exc:
        # 502 rather than 500: Cube being down is not this server malfunctioning, and the
        # message is what the button shows.
        raise HTTPException(502, f"Cube is not answering: {exc}") from exc


@router.post("/{dashboard_id}/layout")
async def set_layout(dashboard_id: str, body: dict) -> dict:
    # The same validator the agent's `set_layout` uses, so the browser and the model cannot
    # disagree about what a legal layout is.
    tiles = await store.tiles(dashboard_id)
    checked, errors = layouts.validate(body.get("spec") or {}, tiles)
    if checked is None:
        raise HTTPException(400, "; ".join(errors))
    version = await store.save_layout(dashboard_id, checked, note=str(body.get("note", "")))
    return {"version": version, "spec": checked}
