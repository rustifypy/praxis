"""What happens to a database that is already on disk.

`CREATE TABLE IF NOT EXISTS` does not diff, so a column added to `db.py` reaches a new file and
misses every existing one. These assert the other half — that `MIGRATIONS` carries the change
across, and that it runs exactly once.
"""

import sqlite3

import pytest

from analyst_agent.chat.store import ChatStore
from analyst_agent.dashboards.store import DashboardStore
from analyst_agent.db import LATEST

# analyst.db as it was before the chain existed: model_chats as a side table, and no
# source_name or branch on chats. Written out rather than imported, because the point of a
# migration test is a shape the current code can no longer produce.
LEGACY = """
CREATE TABLE chats (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, owner_id TEXT NOT NULL,
    cube_url TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL,
    parent_id TEXT, depth INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE model_chats (
    chat_id TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    source_name TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
INSERT INTO chats VALUES ('q1', 'Revenue', 'local', 'http://c', 1.0, 1.0);
INSERT INTO chats VALUES ('m1', 'Editing wip', 'local', 'http://c', 2.0, 2.0);
INSERT INTO events VALUES ('e1', 'q1', 't1', 1, 'user', '{}', NULL, 0);
INSERT INTO model_chats VALUES ('m1', 'chinook', 'wip', 2.0);
"""


def rows(path, sql):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def legacy_db(path, script=LEGACY):
    con = sqlite3.connect(path)
    con.executescript(script)
    con.commit()
    con.close()
    assert rows(path, "PRAGMA user_version")[0]["user_version"] == 0
    return path


async def test_fresh_file_is_stamped_and_runs_no_migration(tmp_path):
    """A file with no tables is new by definition — the schema already has the current shape,
    so replaying migration 1 over it would collide with the column it adds."""
    path = tmp_path / "analyst.db"
    await ChatStore(path).init()

    assert rows(path, "PRAGMA user_version")[0]["user_version"] == LATEST
    assert not rows(path, "SELECT name FROM sqlite_master WHERE name = 'model_chats'")


async def test_legacy_file_moves_model_chats_onto_chats(tmp_path):
    path = legacy_db(tmp_path / "analyst.db")
    store = ChatStore(path)
    await store.init()

    assert rows(path, "PRAGMA user_version")[0]["user_version"] == LATEST
    assert not rows(path, "SELECT name FROM sqlite_master WHERE name = 'model_chats'")
    # The editing chat kept both facts, and the question chat stayed a question chat.
    assert rows(path, "SELECT source_name, branch FROM chats WHERE id = 'm1'") == [
        {"source_name": "chinook", "branch": "wip"}
    ]
    assert rows(path, "SELECT source_name, branch FROM chats WHERE id = 'q1'") == [
        {"source_name": "", "branch": ""}
    ]
    assert [c.id for c in await store.list_chats()] == ["q1"]
    assert await store.model_chat_source("m1") == "chinook"
    assert rows(path, "SELECT count(*) c FROM events")[0]["c"] == 1


async def test_a_database_predating_model_chats_still_migrates(tmp_path):
    """The table is created by the migration before it is read, so a file old enough never to
    have had it is not a crash on DROP TABLE."""
    script = LEGACY.split("CREATE TABLE model_chats")[0] + (
        "INSERT INTO chats VALUES ('q1', 'Revenue', 'local', 'http://c', 1.0, 1.0);"
    )
    path = legacy_db(tmp_path / "analyst.db", script)
    await ChatStore(path).init()

    assert rows(path, "PRAGMA user_version")[0]["user_version"] == LATEST
    assert rows(path, "SELECT source_name FROM chats") == [{"source_name": ""}]


async def test_migrating_twice_changes_nothing(tmp_path):
    path = legacy_db(tmp_path / "analyst.db")
    await ChatStore(path).init()
    before = rows(path, "SELECT * FROM chats ORDER BY id")

    await ChatStore(path).init()
    await DashboardStore(path).init()

    assert rows(path, "SELECT * FROM chats ORDER BY id") == before
    assert rows(path, "PRAGMA user_version")[0]["user_version"] == LATEST


@pytest.mark.parametrize("first", ["dashboards", "chats"])
async def test_either_store_may_open_the_file_first(tmp_path, first):
    """`user_version` belongs to the file, and the lifespan's order is not a guarantee."""
    path = legacy_db(tmp_path / "analyst.db")
    stores = [DashboardStore(path), ChatStore(path)]
    if first == "chats":
        stores.reverse()
    for store in stores:
        await store.init()

    assert rows(path, "PRAGMA user_version")[0]["user_version"] == LATEST
    assert await ChatStore(path).model_chat_source("m1") == "chinook"
    assert rows(path, "SELECT count(*) c FROM dashboards")[0]["c"] == 0


async def test_a_failed_migration_leaves_the_version_alone(tmp_path, monkeypatch):
    """The version bump rides in the migration's own transaction, so a half-applied schema
    cannot be recorded as done."""
    import analyst_agent.db as db

    monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, "SELECT this_is_not_valid_sql!;"))
    monkeypatch.setattr(db, "LATEST", len(db.MIGRATIONS))
    path = legacy_db(tmp_path / "analyst.db")

    with pytest.raises(sqlite3.OperationalError):
        await ChatStore(path).init()

    # Migration 1 committed; the broken one did not, and did not claim its version.
    assert rows(path, "PRAGMA user_version")[0]["user_version"] == 1
    assert rows(path, "SELECT source_name FROM chats WHERE id = 'm1'") == [
        {"source_name": "chinook"}
    ]
