"""The dashboard pages' HTTP surface. Refresh lives here and nowhere else, as a plain POST
since the refresh is atomic and has no partial state to stream.
"""

from dataclasses import asdict

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..agent.build import SecurityContext, load_catalog
from ..config import settings
from ..cube.client import CubeClient, CubeError, mint_token
from ..cube.shape import Column
from ..dashboards import delta
from ..dashboards import layout as layouts
from ..dashboards import run as runner
from ..dashboards import tiles as tilecheck
from ..dashboards.store import DashboardStore

router = APIRouter(prefix="/api/dashboards")

# Resolved server-side and never read off a header — it is signed into the Cube JWT, so a
# trusted header would be the hole under any row-level security added later. The same
# hardcoded local identity `sources.py` uses.
OWNER = SecurityContext(user_id="local")


class NewDashboard(BaseModel):
    title: str = "New dashboard"


class Rename(BaseModel):
    title: str


async def init_state(app) -> None:
    """One store for the process, created at startup like the sources one."""
    store = DashboardStore()
    await store.init()
    app.state.dashboards = store


@router.get("")
async def list_dashboards(request: Request) -> list[dict]:
    store = request.app.state.dashboards
    out = []
    for board in await store.list_dashboards(OWNER.user_id):
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
async def create_dashboard(body: NewDashboard, request: Request) -> dict:
    """Binds to the source the portal is querying; without one its tiles query nothing."""
    active = await runner.active_source_name()
    if not active:
        raise HTTPException(
            400,
            "No registered source is active. Set CUBE_URL to a source in the registry and "
            "restart before creating a dashboard.",
        )
    board = await request.app.state.dashboards.create(body.title, active, OWNER.user_id)
    return board.__dict__


@router.get("/{dashboard_id}")
async def read_dashboard(dashboard_id: str, request: Request) -> dict:
    store = request.app.state.dashboards
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
        stored = {r.tile_id: r for r in await store.results_for_run(last.id)}
        earlier = (
            {r.tile_id: r for r in await store.results_for_run(recent[1].id)}
            if len(recent) > 1
            else {}
        )
        for tile in tiles:
            record = stored.get(tile.id)
            if record is None:
                continue
            columns = [Column(**c) for c in record.columns]
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
                "deltas": [asdict(d) for d in changes],
                "compared_at": since,
            }

    # Asked once. A mismatch also skips the compile pass — checking tiles against another
    # source's catalog reports every member as missing, which reads as a broken dashboard
    # rather than as the wrong database.
    mismatch = await runner.source_mismatch(board.source_name)
    checks = (
        {"available": False, "reason": mismatch, "broken": []}
        if mismatch
        else await _checks(dashboard_id, store)
    )

    return {
        **board.__dict__,
        "tiles": [
            {
                "id": t.id,
                "title": t.title,
                "query": t.query,
                "chart": t.chart,
                "time_member": t.time_member,
            }
            for t in tiles
        ],
        "layout": spec,
        "layout_version": current["version"] if current else None,
        "history": await store.layout_history(dashboard_id),
        "run": (
            None
            if last is None
            else {
                "id": last.id,
                "status": last.status,
                "insight": last.insight,
                "started_at": last.started_at,
                "finished_at": last.finished_at,
            }
        ),
        "results": results,
        "mismatch": mismatch,
        "checks": checks,
    }


async def _checks(dashboard_id: str, store: DashboardStore) -> dict:
    """Compile every tile without executing it, and never fail the page for trying. Turns a
    member dropped by a model rebuild into a banner on arrival."""
    client = CubeClient(mint_token(OWNER.as_claims()))
    try:
        catalog = await load_catalog(client)
        outcome = await runner.validate(dashboard_id, store, catalog, client, settings.max_rows)
    except (CubeError, httpx.RequestError) as exc:
        # `httpx.RequestError`, not `OSError`: httpx transport errors do not subclass it, and an
        # `except OSError` here let a ConnectError through and 500'd the page for a stopped
        # container. Same pair `sources.py` catches.
        return {"available": False, "reason": str(exc), "broken": []}
    finally:
        await client.aclose()
    return {
        "available": True,
        "reason": "",
        "broken": [
            {"tile_id": t.tile_id, "title": t.title, "errors": t.errors}
            for t in outcome.tiles
            if not t.ok
        ],
    }


@router.patch("/{dashboard_id}")
async def rename_dashboard(dashboard_id: str, body: Rename, request: Request) -> dict:
    await request.app.state.dashboards.rename(dashboard_id, body.title)
    return {"ok": True}


@router.delete("/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, request: Request) -> dict:
    await request.app.state.dashboards.delete(dashboard_id)
    return {"ok": True}


@router.post("/{dashboard_id}/refresh")
async def refresh_dashboard(dashboard_id: str, request: Request) -> dict:
    """Execute every tile's stored query. Atomic: all of them compile, or none of them run."""
    store = request.app.state.dashboards
    client = CubeClient(mint_token(OWNER.as_claims()))
    try:
        catalog = await load_catalog(client)
        outcome = await runner.refresh(dashboard_id, store, catalog, client, settings.max_rows)
    except (CubeError, httpx.RequestError) as exc:
        # 502 rather than 500: the container being down is not this server malfunctioning,
        # and the message is what the button shows.
        raise HTTPException(502, f"Cube is not answering: {exc}") from exc
    finally:
        await client.aclose()
    return {
        "ok": outcome.ok,
        "status": outcome.status,
        "run_id": outcome.run_id,
        "error": outcome.error,
        "insight": outcome.insight,
        "tiles": [
            {
                "tile_id": t.tile_id,
                "title": t.title,
                "ok": t.ok,
                "errors": t.errors,
                "warnings": t.warnings,
                "row_count": t.row_count,
                "degraded": t.degraded,
            }
            for t in outcome.tiles
        ],
    }


@router.post("/{dashboard_id}/layout")
async def set_layout(dashboard_id: str, body: dict, request: Request) -> dict:
    """Through the same validator the agent's `set_layout` uses, so the browser and the model
    cannot disagree about what a legal layout is."""
    store = request.app.state.dashboards
    tiles = await store.tiles(dashboard_id)
    checked, errors = layouts.validate(body.get("spec") or {}, tiles)
    if checked is None:
        raise HTTPException(400, "; ".join(errors))
    version = await store.save_layout(dashboard_id, checked, note=str(body.get("note", "")))
    return {"version": version, "spec": checked}
