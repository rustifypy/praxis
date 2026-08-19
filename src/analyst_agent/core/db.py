from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

# One database, one schema. LangGraph's checkpointer keeps its own tables in this same
# file, created by `AsyncSqliteSaver` rather than here.

SCHEMA = """
-- --- the transcript ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chats (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    -- 'question' asks about the data, 'model' edits it. The two agents share this table
    -- and this transcript format and differ only here.
    kind        TEXT NOT NULL DEFAULT 'question',
    -- Model chats only: the branch it was opened on. Provenance, not a binding — one
    -- container means one checkout, so the editor acts on whatever is checked out now.
    branch      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id        TEXT PRIMARY KEY,
    chat_id   TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn_id   TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    type      TEXT NOT NULL,
    data      TEXT NOT NULL,
    parent_id TEXT,
    depth     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS events_by_chat ON events(chat_id, seq);

-- A query's whole result set, which the event log deliberately does not carry: an event
-- holds the first hundred rows so a replayed transcript renders without a fetch, and
-- this holds all of them so the table, the CSV and any future drilldown have something
-- to address. `columns` is the computed header, not a list of member ids — it is derived
-- from the catalog at query time, and the read endpoint has no catalog to redo that with.
-- `data` rather than `rows`: ROWS is a SQLite keyword, and TEXT-holding-JSON named `data`
-- is what the events table already calls it.
CREATE TABLE IF NOT EXISTS results (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn_id    TEXT NOT NULL,
    columns    TEXT NOT NULL,
    data       TEXT NOT NULL,
    row_count  INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS results_by_chat ON results(chat_id);

-- --- dashboards -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dashboards (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    -- No chat_id — a dashboard is editable from any chat.
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

-- --- the data source --------------------------------------------------------------
-- One Cube deployment, so one row: `id = 1` is a CHECK rather than a convention, and
-- credentials are in it because `docker run -e` is built from it and nothing else on disk
-- could then drift from it. `--data-dir` scopes this along with the history, which is the
-- point — a scratch directory is a whole scratch install.
CREATE TABLE IF NOT EXISTS source (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    description TEXT NOT NULL DEFAULT '',
    driver      TEXT NOT NULL,
    credentials TEXT NOT NULL DEFAULT '{}',
    mounts      TEXT NOT NULL DEFAULT '{}',
    steps       TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""


@asynccontextmanager
async def connect(path: Path):
    """One connection, rows as mappings, foreign keys enforced. Creates the parent directory,
    which may not exist on a first run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
