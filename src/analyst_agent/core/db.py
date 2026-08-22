"""One file, one metadata: every table this process creates.

SQLAlchemy Core and not the ORM, deliberately. What the raw SQL cost was the JSON columns —
twenty-nine hand-written `dumps`/`loads` pairs and three row-mappers that existed only to undo
them — and typed columns fix that. What an ORM would add on top is a session lifecycle, an
identity map and lazy relationships, and the access patterns here are all "by id" or "the
children of one parent", so it would buy traversal that async SQLAlchemy then makes you annotate
anyway.

`create_all` does not diff, exactly as `CREATE TABLE IF NOT EXISTS` did not: a new table appears
on an existing file and a new *column* silently does not. There is no migration mechanism and
deliberately so — this runs on one machine for one person, and recreating the file is cheaper
than a chain nobody else will ever run.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    event,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import settings

metadata = MetaData()

# --- the transcript ----------------------------------------------------------

chats = Table(
    "chats",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("kind", String, nullable=False, default="question"),
    Column("ref", String, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("id", String, primary_key=True),
    Column("chat_id", String, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
    Column("turn_id", String, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("type", String, nullable=False),
    Column("data", JSON, nullable=False),
    Column("parent_id", String),
    Column("depth", Integer, nullable=False, default=0),
    Index("events_by_chat", "chat_id", "seq"),
)

results = Table(
    "results",
    metadata,
    Column("id", String, primary_key=True),
    Column("chat_id", String, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
    Column("turn_id", String, nullable=False),
    Column("columns", JSON, nullable=False),
    # `ROWS` is a SQLite keyword, which is why the column is `data` — as it is on `events`.
    Column("data", JSON, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
    Index("results_by_chat", "chat_id"),
)

# --- dashboards --------------------------------------------------------------

dashboards = Table(
    "dashboards",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

tiles = Table(
    "tiles",
    metadata,
    Column("id", String, primary_key=True),
    Column("dashboard_id", String, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
    Column("title", String, nullable=False),
    Column("query", JSON, nullable=False),
    Column("chart", JSON, nullable=False),
    # Written on every tile and read by nothing: it is here because the feature needing it would
    # otherwise require exactly this column, and a column cannot be added later.
    Column("time_member", String, nullable=False, default=""),
    Column("deleted_at", Float),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Index("tiles_by_dashboard", "dashboard_id"),
)

layouts = Table(
    "layouts",
    metadata,
    Column(
        "dashboard_id", String, ForeignKey("dashboards.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("version", Integer, primary_key=True),
    Column("spec", JSON, nullable=False),
    Column("note", String, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("dashboard_id", String, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),
    Column("errors", JSON, nullable=False, default=list),
    Column("started_at", Float, nullable=False),
    Column("finished_at", Float),
)

tile_results = Table(
    "tile_results",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("tile_id", String, ForeignKey("tiles.id", ondelete="CASCADE"), nullable=False),
    Column("columns", JSON, nullable=False),
    # Null once pruned: the run and its row count survive, the rows do not.
    Column("data", JSON),
    Column("row_count", Integer, nullable=False),
    Column("resolved_ranges", JSON, nullable=False, default=dict),
    Column("duration_ms", Integer),
    Column("created_at", Float, nullable=False),
    Index("results_by_run", "run_id"),
)

Index("runs_by_dashboard", runs.c.dashboard_id, runs.c.started_at.desc())
Index("results_by_tile", tile_results.c.tile_id, tile_results.c.created_at.desc())

# --- agent access ------------------------------------------------------------

mcp = Table(
    "mcp",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("enabled", Integer, nullable=False, default=1),
    Column("token", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint("id = 1"),
)

# --- the deployment ----------------------------------------------------------

deployment = Table(
    "deployment",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("store_url", String, nullable=False, default=""),
    Column("store_token", String, nullable=False, default=""),
    Column("store_read_token", String, nullable=False, default=""),
    Column("api_secret", String, nullable=False, default=""),
    Column("sandbox_url", String, nullable=False, default=""),
    Column("sandbox_token", String, nullable=False, default=""),
    Column("driver", String, nullable=False),
    Column("description", String, nullable=False, default=""),
    Column("credentials", JSON, nullable=False, default=dict),
    Column("steps", JSON, nullable=False, default=dict),
    Column("state", JSON, nullable=False, default=dict),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint("id = 1"),
)


# --- opening it --------------------------------------------------------------

_ENGINES: dict[Path, AsyncEngine] = {}


def engine(path: Path) -> AsyncEngine:
    """One engine per file, cached — a pool, so a page that reads five tables opens one
    connection rather than five."""
    if path not in _ENGINES:
        path.parent.mkdir(parents=True, exist_ok=True)
        # `default=str` because a query's rows come from Cube and may hold a value json cannot
        # reach on its own. Without it a single date in a result set fails the whole write.
        built = create_async_engine(
            f"sqlite+aiosqlite:///{path}",
            json_serializer=lambda value: json.dumps(value, default=str),
        )

        @event.listens_for(built.sync_engine, "connect")
        def _pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            # Foreign keys are off by default in SQLite, and the cascades are the delete story.
            cursor.execute("PRAGMA foreign_keys = ON")
            # WAL because LangGraph's checkpointer holds its own raw aiosqlite connections on
            # this same file: a reader and a writer at once is a locked database otherwise, and
            # it would surface as an intermittently failing chat turn.
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.close()

        _ENGINES[path] = built
    return _ENGINES[path]


@asynccontextmanager
async def connect(path: Path | None = None):
    """A connection in a transaction, committed on the way out — so nothing commits by hand.

    The path is settings' unless a caller says otherwise, which is one answer to "which database
    am I talking to" rather than two."""
    async with engine(path or settings.db_path).begin() as conn:
        yield conn


async def create_tables(path: Path | None = None) -> None:
    # Also the file mode, because the mode is a property of *the file* — it holds the warehouse
    # password in plaintext — rather than of whichever caller happens to create it. It used to
    # hang off `DeploymentStore.init`, which left the database 644 between being created by one
    # module and secured by another.
    target = path or settings.db_path
    async with engine(target).begin() as conn:
        await conn.run_sync(metadata.create_all)
    target.chmod(0o600)
