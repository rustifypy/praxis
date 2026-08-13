"""How this process opens SQLite, and every table it creates — in one place, so two stores
in separate files cannot drift on a pragma one of them relies on.

The schema lives here rather than beside each store because **the unit is the file, not the
store**. `analyst.db` is written by `ChatStore` and `DashboardStore` both, so neither owns it
alone; a schema split across the two modules reads as two databases and is one. It also has a
consequence worth knowing before adding versioning: `PRAGMA user_version` is a single integer
per file, so a migration chain belongs to `analyst.db`, never to a store within it.

`CREATE TABLE IF NOT EXISTS` does not diff, so the schema below is only ever the shape of a
**fresh** file. Changing an existing one is what `MIGRATIONS` is for: a column added here and
nowhere else reaches a new database and silently misses every database already on disk.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

# --- analyst.db: the transcript ------------------------------------------------------
# LangGraph's checkpointer keeps its own tables in this same file, created by
# `AsyncSqliteSaver` rather than here.

CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    -- Which Cube this chat ran against. One agent, one Cube today; stored so that
    -- old chats keep their provenance when the tool learns to span instances.
    cube_url   TEXT NOT NULL,
    -- Empty for a question chat; a source name for one that edits that source's model.
    -- The two agents share this table and this transcript format, and differ only here.
    source_name TEXT NOT NULL DEFAULT '',
    -- Which branch the editing chat was opened on. Provenance like `cube_url`, not a
    -- binding: one container means one checkout, so the editor acts on whatever is
    -- checked out now.
    branch     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
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

CREATE INDEX IF NOT EXISTS chats_by_source ON chats(source_name, updated_at DESC);

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
"""

# --- analyst.db: dashboards ----------------------------------------------------------

DASHBOARDS_SCHEMA = """
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

# --- sources.db: the registry --------------------------------------------------------
# Its own file, and deliberately not under DATA_DIR: pointing `--data-dir` at a scratch
# file to protect chat history must not also hide the sources.

SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    driver      TEXT NOT NULL,
    -- One published port per source, and the agent selects one of them by CUBE_URL.
    port        INTEGER NOT NULL UNIQUE,
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


# --- migrations ----------------------------------------------------------------------

MIGRATIONS = (
    # 1: model_chats becomes two columns on chats. The side table existed only because there
    # was no way to add a column to a database already on disk; there is one now. Created
    # first so this also runs on a database old enough never to have had the table.
    """
    CREATE TABLE IF NOT EXISTS model_chats (
        chat_id     TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
        source_name TEXT NOT NULL,
        branch      TEXT NOT NULL DEFAULT '',
        created_at  REAL NOT NULL
    );
    ALTER TABLE chats ADD COLUMN source_name TEXT NOT NULL DEFAULT '';
    ALTER TABLE chats ADD COLUMN branch TEXT NOT NULL DEFAULT '';
    UPDATE chats SET
        source_name = (SELECT source_name FROM model_chats WHERE chat_id = chats.id),
        branch      = (SELECT branch      FROM model_chats WHERE chat_id = chats.id)
    WHERE id IN (SELECT chat_id FROM model_chats);
    DROP TABLE model_chats;
    CREATE INDEX IF NOT EXISTS chats_by_source ON chats(source_name, updated_at DESC);
    """,
)
"""Applied in order to a database already on disk; a script's index plus one is the
`user_version` it produces. A shipped migration is never edited — a database that has run it
will not run it again, so a correction is the next entry rather than a change to this one."""

LATEST = len(MIGRATIONS)


async def migrate(db: aiosqlite.Connection) -> int:
    """Bring one file up to `LATEST`, and answer the version it is now.

    Runs *before* the `CREATE TABLE IF NOT EXISTS` scripts, not after: migration 1 adds a column
    the schema above already declares, so on a fresh file the ALTER would collide with the table
    it was meant to change. A file with no tables at all is fresh by definition — it is stamped
    and skipped, and the schema then creates it at the current shape.

    Each script is run with its own version bump in one transaction, so a migration that fails
    part-way leaves the file at the version it already had rather than half-changed.

    `user_version` belongs to the file, not to a store within it — which is why this takes a
    connection rather than a path. `analyst.db` is opened by two stores and either may be first.
    """
    version = (await (await db.execute("PRAGMA user_version")).fetchone())[0]
    if version >= LATEST:
        return version
    empty = not await (
        await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ).fetchone()
    if empty:
        await db.executescript(f"PRAGMA user_version = {LATEST};")
        return LATEST
    for n, script in enumerate(MIGRATIONS[version:], start=version + 1):
        await db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {n};\nCOMMIT;")
    return LATEST
