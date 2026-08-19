"""`db.apply` against a database that predates a migration, and against one it creates.

The two halves are the whole risk. `CREATE TABLE IF NOT EXISTS` silently skips a table that
already exists, so only a migration removes anything — and a migration that removes a column
raises on a database created after the column was gone, which is why a new file is stamped
rather than migrated.
"""

import sqlite3

from analyst_agent.core.db import MIGRATIONS, apply, connect


def _version(path) -> int:
    with sqlite3.connect(path) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def _columns(path, table: str) -> list[str]:
    with sqlite3.connect(path) as db:
        return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _old_shape(path) -> None:
    """The parts of a pre-migration database that migration 1 acts on, plus a run to prove
    the rows outlive the columns being dropped. The columns `SCHEMA`'s indexes name have to
    be here too — `CREATE INDEX IF NOT EXISTS` still runs against a table it did not create.
    """
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE dashboards (id TEXT PRIMARY KEY, title TEXT NOT NULL);
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, dashboard_id TEXT, status TEXT,
                insight TEXT NOT NULL, started_at REAL
            );
            CREATE TABLE tile_results (
                id TEXT PRIMARY KEY, run_id TEXT, tile_id TEXT,
                insight TEXT NOT NULL, created_at REAL
            );
            CREATE TABLE windows (dashboard_id TEXT PRIMARY KEY, date_range TEXT NOT NULL);
        """)
        db.execute("INSERT INTO runs VALUES ('r1', 'd1', 'ok', 'revenue held steady', 1.0)")
        db.execute("INSERT INTO tile_results VALUES ('t1', 'r1', 'tile', 'a caption', 1.0)")
        db.execute("INSERT INTO windows VALUES ('d1', '\"last 30 days\"')")


async def test_an_existing_database_loses_the_storage_and_keeps_the_rows(tmp_path):
    path = tmp_path / "analyst.db"
    _old_shape(path)

    async with connect(path) as db:
        await apply(db)

    assert "windows" not in _tables(path)
    assert "insight" not in _columns(path, "runs")
    assert "insight" not in _columns(path, "tile_results")
    with sqlite3.connect(path) as raw:
        assert raw.execute("SELECT id, status FROM runs").fetchall() == [("r1", "ok")]
    assert _version(path) == len(MIGRATIONS)


async def test_applying_twice_changes_nothing(tmp_path):
    path = tmp_path / "analyst.db"
    _old_shape(path)

    async with connect(path) as db:
        await apply(db)
    before = _tables(path), _columns(path, "runs"), _version(path)
    async with connect(path) as db:
        await apply(db)

    assert (_tables(path), _columns(path, "runs"), _version(path)) == before


async def test_a_new_database_is_stamped_rather_than_migrated(tmp_path):
    """`SCHEMA` builds it in its final shape, so migration 1 would raise on the column it
    drops. The stamp is what keeps it from running at all."""
    path = tmp_path / "analyst.db"

    async with connect(path) as db:
        await apply(db)

    assert _version(path) == len(MIGRATIONS)
    assert "windows" not in _tables(path)
    assert "insight" not in _columns(path, "runs")
