"""The dashboards, their tiles, their layout history, their runs — and the layout rules.

Functions and not a store class: the only state one held was a path, every caller in the app
used the default, and nothing ever substituted a double for it. Which database this talks to is
`settings.db_path`, in one place.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

import sqlalchemy as sa
from pydantic import validate_call

from . import db

ROWS_KEPT = 2
Status = Literal["validating", "running", "ok", "refused", "failed"]


@dataclass
class Dashboard:
    id: str
    title: str
    created_at: float
    updated_at: float


@dataclass
class Tile:
    id: str
    dashboard_id: str
    title: str
    query: dict
    chart: dict
    time_member: str
    created_at: float
    updated_at: float
    deleted_at: float | None = None


@dataclass
class Run:
    id: str
    dashboard_id: str
    status: str
    errors: list[dict] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float | None = None


@dataclass
class TileResult:
    id: str
    run_id: str
    tile_id: str
    columns: list[dict]
    # None once pruned: the run and its row count survive, the rows do not.
    rows: list[dict] | None
    row_count: int
    resolved_ranges: dict[str, list[str]]
    duration_ms: int | None
    created_at: float


# --- dashboards --------------------------------------------------------------


async def create(title: str) -> Dashboard:
    now = time.time()
    board = Dashboard(id=uuid.uuid4().hex, title=title, created_at=now, updated_at=now)
    async with db.connect() as conn:
        await conn.execute(sa.insert(db.dashboards).values(**board.__dict__))
    return board


# Not `list` — that name shadows the builtin for every annotation below it.
async def list_dashboards() -> list[Dashboard]:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.dashboards).order_by(db.dashboards.c.updated_at.desc())
        )
    return [Dashboard(**row) for row in found.mappings()]


async def get(dashboard_id: str) -> Dashboard | None:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.dashboards).where(db.dashboards.c.id == dashboard_id)
        )
    row = found.mappings().first()
    return Dashboard(**row) if row else None


async def rename(dashboard_id: str, title: str) -> None:
    async with db.connect() as conn:
        await conn.execute(
            sa.update(db.dashboards)
            .where(db.dashboards.c.id == dashboard_id)
            .values(title=title, updated_at=time.time())
        )


async def delete_dashboard(dashboard_id: str) -> None:
    # The one hard delete here; everything below it cascades.
    async with db.connect() as conn:
        await conn.execute(sa.delete(db.dashboards).where(db.dashboards.c.id == dashboard_id))


# --- tiles -------------------------------------------------------------------


async def add_tile(
    dashboard_id: str,
    title: str,
    query: dict,
    chart: dict,
    time_member: str = "",
) -> Tile:
    now = time.time()
    tile_row = Tile(
        id=uuid.uuid4().hex,
        dashboard_id=dashboard_id,
        title=title,
        query=query,
        chart=chart,
        time_member=time_member,
        created_at=now,
        updated_at=now,
    )
    async with db.connect() as conn:
        await conn.execute(sa.insert(db.tiles).values(**tile_row.__dict__))
        await _touched(conn, dashboard_id, now)
    return tile_row


async def tiles(dashboard_id: str, include_deleted: bool = False) -> list[Tile]:
    query = sa.select(db.tiles).where(db.tiles.c.dashboard_id == dashboard_id)
    if not include_deleted:
        query = query.where(db.tiles.c.deleted_at.is_(None))
    async with db.connect() as conn:
        found = await conn.execute(query.order_by(db.tiles.c.created_at))
    return [Tile(**row) for row in found.mappings()]


async def tile(tile_id: str) -> Tile | None:
    async with db.connect() as conn:
        found = await conn.execute(sa.select(db.tiles).where(db.tiles.c.id == tile_id))
    row = found.mappings().first()
    return Tile(**row) if row else None


async def update_tile(
    tile_id: str,
    title: str | None = None,
    query: dict | None = None,
    chart: dict | None = None,
    time_member: str | None = None,
) -> None:
    # Patch semantics: None leaves a field alone.
    changed = {
        column: value
        for column, value in (
            ("title", title),
            ("query", query),
            ("chart", chart),
            ("time_member", time_member),
        )
        if value is not None
    }
    if not changed:
        return
    async with db.connect() as conn:
        await conn.execute(
            sa.update(db.tiles)
            .where(db.tiles.c.id == tile_id)
            .values(**changed, updated_at=time.time())
        )


async def set_tile_deleted(tile_id: str, deleted: bool = True) -> None:
    now = time.time()
    async with db.connect() as conn:
        await conn.execute(
            sa.update(db.tiles)
            .where(db.tiles.c.id == tile_id)
            .values(deleted_at=now if deleted else None, updated_at=now)
        )


# --- layouts -----------------------------------------------------------------


async def save_layout(dashboard_id: str, spec: dict, note: str = "") -> int:
    # Append-only: there is no update path, and that absence is the undo.
    now = time.time()
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(db.layouts.c.version), 0) + 1).where(
                db.layouts.c.dashboard_id == dashboard_id
            )
        )
        version = int(found.scalar_one())
        await conn.execute(
            sa.insert(db.layouts).values(
                dashboard_id=dashboard_id,
                version=version,
                spec=spec,
                note=note,
                created_at=now,
            )
        )
        await _touched(conn, dashboard_id, now)
    return version


async def layout(dashboard_id: str, version: int | None = None) -> dict | None:
    query = sa.select(db.layouts).where(db.layouts.c.dashboard_id == dashboard_id)
    query = (
        query.order_by(db.layouts.c.version.desc()).limit(1)
        if version is None
        else query.where(db.layouts.c.version == version)
    )
    async with db.connect() as conn:
        found = await conn.execute(query)
    row = found.mappings().first()
    if row is None:
        return None
    return {
        "version": row["version"],
        "spec": row["spec"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


async def layout_history(dashboard_id: str) -> list[dict]:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.layouts.c.version, db.layouts.c.note, db.layouts.c.created_at)
            .where(db.layouts.c.dashboard_id == dashboard_id)
            .order_by(db.layouts.c.version.desc())
        )
    return [dict(row) for row in found.mappings()]


# --- runs --------------------------------------------------------------------


async def start_run(dashboard_id: str) -> Run:
    run = Run(
        id=uuid.uuid4().hex,
        dashboard_id=dashboard_id,
        status="validating",
        started_at=time.time(),
    )
    async with db.connect() as conn:
        await conn.execute(sa.insert(db.runs).values(**run.__dict__))
    return run


@validate_call
async def finish_run(run_id: str, status: Status, errors: list[dict] | None = None) -> None:
    async with db.connect() as conn:
        await conn.execute(
            sa.update(db.runs)
            .where(db.runs.c.id == run_id)
            .values(status=status, errors=errors or [], finished_at=time.time())
        )


@validate_call
async def set_run_status(run_id: str, status: Status) -> None:
    async with db.connect() as conn:
        await conn.execute(sa.update(db.runs).where(db.runs.c.id == run_id).values(status=status))


async def runs(dashboard_id: str, limit: int = 20, status: str | None = None) -> list[Run]:
    # `status` filters, which is how a caller asks for the last two runs that produced data
    # without paging past the failures between them.
    query = sa.select(db.runs).where(db.runs.c.dashboard_id == dashboard_id)
    if status:
        query = query.where(db.runs.c.status == status)
    async with db.connect() as conn:
        found = await conn.execute(query.order_by(db.runs.c.started_at.desc()).limit(limit))
    return [Run(**row) for row in found.mappings()]


async def latest_run(dashboard_id: str, status: str = "ok") -> Run | None:
    # A dashboard renders the last run that produced data, not the last attempt: a refused
    # refresh must not blank the tiles it never touched.
    found = await runs(dashboard_id, limit=1, status=status)
    return found[0] if found else None


# --- results -----------------------------------------------------------------


async def save_tile_result(
    run_id: str,
    tile_id: str,
    columns: list[dict],
    rows: list[dict],
    resolved_ranges: dict[str, list[str]] | None = None,
    duration_ms: int | None = None,
) -> str:
    result_id = uuid.uuid4().hex
    async with db.connect() as conn:
        await conn.execute(
            sa.insert(db.tile_results).values(
                id=result_id,
                run_id=run_id,
                tile_id=tile_id,
                columns=columns,
                data=rows,
                row_count=len(rows),
                resolved_ranges=resolved_ranges or {},
                duration_ms=duration_ms,
                created_at=time.time(),
            )
        )
    return result_id


async def results_for_run(run_id: str) -> list[TileResult]:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.tile_results).where(db.tile_results.c.run_id == run_id)
        )
    return [_result(row) for row in found.mappings()]


async def tile_history(tile_id: str, limit: int = 2) -> list[TileResult]:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.tile_results)
            .where(db.tile_results.c.tile_id == tile_id)
            .order_by(db.tile_results.c.created_at.desc())
            .limit(limit)
        )
    return [_result(row) for row in found.mappings()]


async def prune(dashboard_id: str, keep: int = ROWS_KEPT) -> int:
    # Blanks the rows but keeps the record: a refresh missing from the log would lie about how
    # often anyone refreshes. Raw text because the window function is the whole query — writing
    # it as `sa.select()` would be longer and say less.
    async with db.connect() as conn:
        done = await conn.execute(
            sa.text(
                "UPDATE tile_results SET data = NULL WHERE data IS NOT NULL AND id NOT IN ("
                "  SELECT id FROM ("
                "    SELECT r.id, ROW_NUMBER() OVER ("
                "      PARTITION BY r.tile_id ORDER BY r.created_at DESC"
                "    ) AS rank FROM tile_results r"
                "    JOIN tiles t ON t.id = r.tile_id WHERE t.dashboard_id = :board"
                "  ) WHERE rank <= :keep"
                ") AND tile_id IN (SELECT id FROM tiles WHERE dashboard_id = :board)"
            ),
            {"board": dashboard_id, "keep": keep},
        )
        return done.rowcount


async def _touched(conn, dashboard_id: str, when: float) -> None:
    # Two writers of `dashboards.updated_at`, so one place decides what counts as a touch.
    await conn.execute(
        sa.update(db.dashboards).where(db.dashboards.c.id == dashboard_id).values(updated_at=when)
    )


def _result(row) -> TileResult:
    # The one mapper left: `data` is the column and `rows` is the field, because `ROWS` is a
    # SQLite keyword.
    return TileResult(**{k: v for k, v in row.items() if k != "data"}, rows=row["data"])


# --- layout ------------------------------------------------------------------
#
# Geometry over the rows above rather than a module of its own: it reads nothing but
# `Tile`, and both the agent arranging a board and the page drawing one ask it.

# Past three, a tile is too narrow for a time axis with readable ticks: `ResultChart`'s label
# thinning is sized for a full-width block.
MAX_COLUMNS = 3

# A table's columns are its content, so a half-width one is a horizontal scrollbar.
FULL_WIDTH = ("table", "heatmap")

HEADLINE = ("kpi", "stat", "gauge")


def default_layout(tiles: list[Tile], columns: int = 2) -> dict:
    # Headline numbers first — the summary a dashboard is opened for. Everything else keeps
    # creation order, which is the order the tiles were thought of.
    headline = [tile for tile in tiles if tile.chart.get("type") in HEADLINE]
    rest = [tile for tile in tiles if tile.chart.get("type") not in HEADLINE]
    return {
        "columns": columns,
        "items": [
            {
                "tile": tile.id,
                "span": columns if tile.chart.get("type") in FULL_WIDTH else 1,
            }
            for tile in [*headline, *rest]
        ],
    }


def validate_layout(spec: dict, tiles: list[Tile]) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    by_id = {tile.id: tile for tile in tiles}

    columns = spec.get("columns", 2)
    if not isinstance(columns, int) or not 1 <= columns <= MAX_COLUMNS:
        errors.append(f"columns must be a whole number from 1 to {MAX_COLUMNS} — got {columns!r}")
        columns = 2

    raw_items = spec.get("items")
    if not isinstance(raw_items, list):
        return None, [*errors, "items must be a list of {tile, span} objects"]

    items: list[dict] = []
    placed: set[str] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            errors.append(f"each item must be an object with a tile id — got {entry!r}")
            continue
        tile_id = str(entry.get("tile", ""))
        if tile_id not in by_id:
            errors.append(f"tile {tile_id!r} is not on this dashboard")
            continue
        if tile_id in placed:
            errors.append(f"tile {tile_id!r} is placed twice")
            continue
        span = entry.get("span", 1)
        if not isinstance(span, int) or not 1 <= span <= columns:
            errors.append(
                f"span for {by_id[tile_id].title!r} must be from 1 to {columns} — got {span!r}"
            )
            continue
        placed.add(tile_id)
        items.append({"tile": tile_id, "span": span})

    if errors:
        return None, errors

    # Appended in creation order, so an unmentioned tile lands where it would have been.
    for tile in tiles:
        if tile.id not in placed:
            items.append(
                {"tile": tile.id, "span": columns if tile.chart.get("type") in FULL_WIDTH else 1}
            )

    if not items:
        return None, ["a layout needs at least one tile"]
    return {"columns": columns, "items": items}, []
