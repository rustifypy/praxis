"""The eight dashboard routes.

`/{dashboard_id}` is a path parameter, so every fixed-segment route stays above it —
`test_surface.py` asserts the ordering.
"""

from fastapi import APIRouter, HTTPException, Request

from ...core import boards
from ...modelstore.client import StoreError
from ..support import state
from . import controller
from .dto import Layout, NewDashboard, Rename

router = APIRouter(prefix="/api/dashboards")


@router.get("")
async def list_dashboards() -> list[dict]:
    """Every board with its tile count and when it was last refreshed."""
    return await controller.listing()


@router.post("")
async def create_dashboard(body: NewDashboard) -> dict:
    """An empty board. It has no tiles until the agent adds some."""
    return (await boards.create(body.title)).__dict__


@router.get("/{dashboard_id}")
async def read_dashboard(dashboard_id: str, request: Request) -> dict:
    """The whole board: tiles, layout, the last run's rows and the deltas against the one
    before it, plus a compile pass so a model rebuild that broke a tile is a banner on arrival."""
    found = await controller.read(dashboard_id, await state.cube(request.app))
    if found is None:
        raise HTTPException(404, f"no dashboard with id {dashboard_id!r}")
    return found


@router.patch("/{dashboard_id}")
async def rename_dashboard(dashboard_id: str, body: Rename) -> dict:
    await boards.rename(dashboard_id, body.title)
    return {"ok": True}


@router.delete("/{dashboard_id}")
async def delete_dashboard(dashboard_id: str) -> dict:
    await boards.delete_dashboard(dashboard_id)
    return {"ok": True}


@router.post("/{dashboard_id}/refresh")
async def refresh_dashboard(dashboard_id: str, request: Request) -> dict:
    """Run every tile. Atomic: all of them compile, or none of them execute."""
    try:
        return await controller.run(dashboard_id, await state.cube(request.app))
    except StoreError as exc:
        # 502 rather than 500: Cube being down is not this server malfunctioning, and the
        # message is what the button shows.
        raise HTTPException(502, f"Cube is not answering: {exc}") from exc


@router.post("/{dashboard_id}/layout")
async def set_layout(dashboard_id: str, body: Layout) -> dict:
    """Place the tiles. The same validator the agent's `set_layout` runs, so the browser and
    the model cannot disagree about what a legal layout is."""
    checked, errors = boards.validate_layout(body.spec, await boards.tiles(dashboard_id))
    if checked is None:
        raise HTTPException(400, "; ".join(errors))
    version = await boards.save_layout(dashboard_id, checked, note=body.note)
    return {"version": version, "spec": checked}
