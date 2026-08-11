"""Dashboards, tiles, layouts and refresh history, in `analyst.db`. **These tables will never
gain a column** — `CREATE TABLE IF NOT EXISTS` does not diff, so adding one fails silently.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from ..db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboards (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    owner_id    TEXT NOT NULL,
    -- Which source these queries mean something against, compared with the active source
    -- before any refresh: two warehouses can both have `sales.revenue` and nothing would
    -- error on the wrong one. No chat_id — a dashboard is editable from any chat.
    source_name TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tiles (
    id           TEXT PRIMARY KEY,
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    -- A copy of the Cube JSON, not a pointer into the event log, so deleting the chat a
    -- tile was born in cannot take the tile with it.
    query        TEXT NOT NULL,
    chart        TEXT NOT NULL,
    -- Which timeDimension a dashboard-level date control would write into. Unread today;
    -- without the column that control is unbuildable.
    time_member  TEXT NOT NULL DEFAULT '',
    -- Soft, so reverting a layout can bring a deleted tile back.
    deleted_at   REAL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

-- Versioned apart from tiles: rearranging is the edit undo exists for, and recording it
-- must not duplicate twelve stored queries. A version naming a since-deleted tile is
-- normal and renders as the tiles that survive.
CREATE TABLE IF NOT EXISTS layouts (
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    spec         TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL,
    PRIMARY KEY (dashboard_id, version)
);

-- A refresh is an object because it is atomic: it either happened or it did not, and
-- "as of" is one timestamp for the board rather than one per tile.
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,
    errors       TEXT NOT NULL DEFAULT '[]',
    insight      TEXT NOT NULL DEFAULT '',
    started_at   REAL NOT NULL,
    finished_at  REAL
);

CREATE TABLE IF NOT EXISTS tile_results (
    id             TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tile_id        TEXT NOT NULL REFERENCES tiles(id) ON DELETE CASCADE,
    -- Cube returns a grouped time dimension under two keys, so a header recovered from the
    -- rows invents a duplicate column. Stored, like `results.columns`.
    columns        TEXT NOT NULL,
    -- Nullable so pruning drops rows and keeps the history.
    data           TEXT,
    row_count      INTEGER NOT NULL,
    -- What Cube resolved each dateRange to. The only thing that says whether a refresh
    -- moved a window.
    resolved_ranges TEXT NOT NULL DEFAULT '{}',
    duration_ms    INTEGER,
    insight        TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS tiles_by_dashboard ON tiles(dashboard_id);
CREATE INDEX IF NOT EXISTS runs_by_dashboard  ON runs(dashboard_id, started_at DESC);
CREATE INDEX IF NOT EXISTS results_by_run     ON tile_results(run_id);
CREATE INDEX IF NOT EXISTS results_by_tile    ON tile_results(tile_id, created_at DESC);
"""

ROWS_KEPT = 2
"""Runs deep a tile keeps its rows — what a delta against the previous refresh needs."""

STATUSES = ("validating", "running", "ok", "refused", "failed")
"""`refused` is tiles that did not compile, and nothing ran; `failed` is tiles that compiled
and then broke. Separate because only the first is fixable by editing a tile.
"""


@dataclass
class Dashboard:
    id: str
    title: str
    owner_id: str
    source_name: str
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
    insight: str = ""
    started_at: float = 0.0
    finished_at: float | None = None


@dataclass
class TileResult:
    id: str
    run_id: str
    tile_id: str
    columns: list[dict]
    rows: list[dict] | None
    """`None` once pruned — the run and its row count survive, the rows do not."""
    row_count: int
    resolved_ranges: dict[str, list[str]]
    duration_ms: int | None
    insight: str
    created_at: float


class DashboardStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.db_path

    def _db(self):
        return connect(self.path)

    async def init(self) -> None:
        async with self._db() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    # --- dashboards ---------------------------------------------------------------

    async def create(self, title: str, source_name: str, owner_id: str = "local") -> Dashboard:
        now = time.time()
        board = Dashboard(
            id=uuid.uuid4().hex,
            title=title,
            owner_id=owner_id,
            source_name=source_name,
            created_at=now,
            updated_at=now,
        )
        async with self._db() as db:
            await db.execute(
                "INSERT INTO dashboards (id, title, owner_id, source_name, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (board.id, title, owner_id, source_name, now, now),
            )
            await db.commit()
        return board

    # Not `list` — that name shadows the builtin for every annotation later in the class body.
    async def list_dashboards(self, owner_id: str = "local") -> list[Dashboard]:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM dashboards WHERE owner_id = ? ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [Dashboard(**dict(r)) for r in rows]

    async def get(self, dashboard_id: str) -> Dashboard | None:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)
            )
        return Dashboard(**dict(rows[0])) if rows else None

    async def rename(self, dashboard_id: str, title: str) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE dashboards SET title = ?, updated_at = ? WHERE id = ?",
                (title, time.time(), dashboard_id),
            )
            await db.commit()

    async def delete(self, dashboard_id: str) -> None:
        """The one hard delete here. Everything below it cascades."""
        async with self._db() as db:
            await db.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
            await db.commit()

    # --- tiles --------------------------------------------------------------------

    async def add_tile(
        self,
        dashboard_id: str,
        title: str,
        query: dict,
        chart: dict,
        time_member: str = "",
    ) -> Tile:
        now = time.time()
        tile = Tile(
            id=uuid.uuid4().hex,
            dashboard_id=dashboard_id,
            title=title,
            query=query,
            chart=chart,
            time_member=time_member,
            created_at=now,
            updated_at=now,
        )
        async with self._db() as db:
            await db.execute(
                "INSERT INTO tiles (id, dashboard_id, title, query, chart, time_member, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tile.id,
                    dashboard_id,
                    title,
                    json.dumps(query, default=str),
                    json.dumps(chart, default=str),
                    time_member,
                    now,
                    now,
                ),
            )
            await db.execute(
                "UPDATE dashboards SET updated_at = ? WHERE id = ?", (now, dashboard_id)
            )
            await db.commit()
        return tile

    async def tiles(self, dashboard_id: str, include_deleted: bool = False) -> list[Tile]:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        async with self._db() as db:
            rows = await db.execute_fetchall(
                f"SELECT * FROM tiles WHERE dashboard_id = ?{clause} ORDER BY created_at",
                (dashboard_id,),
            )
        return [_tile(r) for r in rows]

    async def tile(self, tile_id: str) -> Tile | None:
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM tiles WHERE id = ?", (tile_id,))
        return _tile(rows[0]) if rows else None

    async def update_tile(
        self,
        tile_id: str,
        title: str | None = None,
        query: dict | None = None,
        chart: dict | None = None,
        time_member: str | None = None,
    ) -> None:
        """Patch semantics: `None` leaves a field alone."""
        sets: list[str] = []
        values: list[object] = []
        for column, value in (
            ("title", title),
            ("query", json.dumps(query, default=str) if query is not None else None),
            ("chart", json.dumps(chart, default=str) if chart is not None else None),
            ("time_member", time_member),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                values.append(value)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(time.time())
        async with self._db() as db:
            await db.execute(f"UPDATE tiles SET {', '.join(sets)} WHERE id = ?", (*values, tile_id))
            await db.commit()

    async def set_tile_deleted(self, tile_id: str, deleted: bool = True) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE tiles SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (time.time() if deleted else None, time.time(), tile_id),
            )
            await db.commit()

    # --- layouts ------------------------------------------------------------------

    async def save_layout(self, dashboard_id: str, spec: dict, note: str = "") -> int:
        """Append a version and return it. There is no update path — that is the undo."""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM layouts WHERE dashboard_id = ?",
                (dashboard_id,),
            )
            version = int(rows[0]["next"])
            now = time.time()
            await db.execute(
                "INSERT INTO layouts (dashboard_id, version, spec, note, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (dashboard_id, version, json.dumps(spec, default=str), note, now),
            )
            await db.execute(
                "UPDATE dashboards SET updated_at = ? WHERE id = ?", (now, dashboard_id)
            )
            await db.commit()
        return version

    async def layout(self, dashboard_id: str, version: int | None = None) -> dict | None:
        """The current layout, or a named earlier one. Current is the highest version."""
        async with self._db() as db:
            if version is None:
                rows = await db.execute_fetchall(
                    "SELECT * FROM layouts WHERE dashboard_id = ? ORDER BY version DESC LIMIT 1",
                    (dashboard_id,),
                )
            else:
                rows = await db.execute_fetchall(
                    "SELECT * FROM layouts WHERE dashboard_id = ? AND version = ?",
                    (dashboard_id, version),
                )
        if not rows:
            return None
        record = rows[0]
        return {
            "version": record["version"],
            "spec": json.loads(record["spec"]),
            "note": record["note"],
            "created_at": record["created_at"],
        }

    async def layout_history(self, dashboard_id: str) -> list[dict]:
        """Every version, newest first, without the specs — the undo menu."""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT version, note, created_at FROM layouts WHERE dashboard_id = ? "
                "ORDER BY version DESC",
                (dashboard_id,),
            )
        return [dict(r) for r in rows]

    # --- runs ---------------------------------------------------------------------

    async def start_run(self, dashboard_id: str) -> Run:
        run = Run(
            id=uuid.uuid4().hex,
            dashboard_id=dashboard_id,
            status="validating",
            started_at=time.time(),
        )
        async with self._db() as db:
            await db.execute(
                "INSERT INTO runs (id, dashboard_id, status, started_at) VALUES (?, ?, ?, ?)",
                (run.id, dashboard_id, run.status, run.started_at),
            )
            await db.commit()
        return run

    async def finish_run(
        self,
        run_id: str,
        status: str,
        errors: list[dict] | None = None,
        insight: str = "",
    ) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown run status {status!r} — one of {', '.join(STATUSES)}")
        async with self._db() as db:
            await db.execute(
                "UPDATE runs SET status = ?, errors = ?, insight = ?, finished_at = ? WHERE id = ?",
                (status, json.dumps(errors or [], default=str), insight, time.time(), run_id),
            )
            await db.commit()

    async def set_run_status(self, run_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown run status {status!r} — one of {', '.join(STATUSES)}")
        async with self._db() as db:
            await db.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
            await db.commit()

    async def runs(
        self, dashboard_id: str, limit: int = 20, status: str | None = None
    ) -> list[Run]:
        """Newest first. `status` filters, which is how a caller asks for the last two runs
        that actually produced data without paging past the failures between them."""
        clause = " AND status = ?" if status else ""
        params: tuple = (dashboard_id, status, limit) if status else (dashboard_id, limit)
        async with self._db() as db:
            rows = await db.execute_fetchall(
                f"SELECT * FROM runs WHERE dashboard_id = ?{clause} "
                "ORDER BY started_at DESC LIMIT ?",
                params,
            )
        return [_run(r) for r in rows]

    async def latest_run(self, dashboard_id: str, status: str = "ok") -> Run | None:
        """What a dashboard renders is the last run that produced data, not the last attempt
        — a refused refresh must not blank the tiles it never touched."""
        found = await self.runs(dashboard_id, limit=1, status=status)
        return found[0] if found else None

    # --- results ------------------------------------------------------------------

    async def save_tile_result(
        self,
        run_id: str,
        tile_id: str,
        columns: list[dict],
        rows: list[dict],
        resolved_ranges: dict[str, list[str]] | None = None,
        duration_ms: int | None = None,
        insight: str = "",
    ) -> str:
        result_id = uuid.uuid4().hex
        async with self._db() as db:
            await db.execute(
                "INSERT INTO tile_results (id, run_id, tile_id, columns, data, row_count, "
                "resolved_ranges, duration_ms, insight, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    run_id,
                    tile_id,
                    json.dumps(columns, default=str),
                    json.dumps(rows, default=str),
                    len(rows),
                    json.dumps(resolved_ranges or {}, default=str),
                    duration_ms,
                    insight,
                    time.time(),
                ),
            )
            await db.commit()
        return result_id

    async def results_for_run(self, run_id: str) -> list[TileResult]:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM tile_results WHERE run_id = ?", (run_id,)
            )
        return [_result(r) for r in rows]

    async def tile_history(self, tile_id: str, limit: int = 2) -> list[TileResult]:
        """This tile's recent results, newest first. Two is a delta."""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM tile_results WHERE tile_id = ? ORDER BY created_at DESC LIMIT ?",
                (tile_id, limit),
            )
        return [_result(r) for r in rows]

    async def prune(self, dashboard_id: str, keep: int = ROWS_KEPT) -> int:
        """Empty the rows of results older than the last `keep` per tile, keeping the record — a
        refresh missing from the log would lie about how often anyone refreshes.
        """
        async with self._db() as db:
            cursor = await db.execute(
                "UPDATE tile_results SET data = NULL WHERE data IS NOT NULL AND id NOT IN ("
                "  SELECT id FROM ("
                "    SELECT r.id, ROW_NUMBER() OVER ("
                "      PARTITION BY r.tile_id ORDER BY r.created_at DESC"
                "    ) AS rank FROM tile_results r"
                "    JOIN tiles t ON t.id = r.tile_id WHERE t.dashboard_id = ?"
                "  ) WHERE rank <= ?"
                ") AND tile_id IN (SELECT id FROM tiles WHERE dashboard_id = ?)",
                (dashboard_id, keep, dashboard_id),
            )
            await db.commit()
            return cursor.rowcount


def _tile(row) -> Tile:
    return Tile(
        id=row["id"],
        dashboard_id=row["dashboard_id"],
        title=row["title"],
        query=json.loads(row["query"]),
        chart=json.loads(row["chart"]),
        time_member=row["time_member"],
        deleted_at=row["deleted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _run(row) -> Run:
    return Run(
        id=row["id"],
        dashboard_id=row["dashboard_id"],
        status=row["status"],
        errors=json.loads(row["errors"]),
        insight=row["insight"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _result(row) -> TileResult:
    return TileResult(
        id=row["id"],
        run_id=row["run_id"],
        tile_id=row["tile_id"],
        columns=json.loads(row["columns"]),
        rows=json.loads(row["data"]) if row["data"] is not None else None,
        row_count=row["row_count"],
        resolved_ranges=json.loads(row["resolved_ranges"]),
        duration_ms=row["duration_ms"],
        insight=row["insight"],
        created_at=row["created_at"],
    )
