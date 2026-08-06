"""Chat history and the LangGraph checkpointer, sharing one SQLite file.

The event log *is* the transcript. Replaying a chat replays its events, which means
provenance — which query ran, what SQL, what the gate rejected — survives a server
restart instead of dying with a per-request Session.

Deltas never reach here. The service coalesces a turn's streamed fragments into one
row per block before appending, so a reopened chat is a handful of reads.
"""

import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..agent.tools import QueryRecord
from ..config import settings
from .events import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    owner_id   TEXT NOT NULL,
    -- Which Cube this chat ran against. One agent, one Cube today; stored so that
    -- old chats keep their provenance when the tool learns to span instances.
    cube_url   TEXT NOT NULL,
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
"""


@dataclass
class Chat:
    id: str
    title: str
    owner_id: str
    cube_url: str
    created_at: float
    updated_at: float


class ChatStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.db_path

    @asynccontextmanager
    async def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def init(self) -> None:
        async with self._db() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def create(self, title: str = "New chat", owner_id: str = "local") -> Chat:
        now = time.time()
        chat = Chat(
            id=uuid.uuid4().hex,
            title=title,
            owner_id=owner_id,
            cube_url=settings.cube_url,
            created_at=now,
            updated_at=now,
        )
        async with self._db() as db:
            await db.execute(
                "INSERT INTO chats VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chat.id,
                    chat.title,
                    chat.owner_id,
                    chat.cube_url,
                    chat.created_at,
                    chat.updated_at,
                ),
            )
            await db.commit()
        return chat

    # Not `list` — a method of that name shadows the builtin for every annotation
    # later in the class body, and `list[dict]` then fails at import time.
    async def list_chats(self, owner_id: str = "local") -> list[Chat]:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM chats WHERE owner_id = ? ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [Chat(**dict(r)) for r in rows]

    async def get(self, chat_id: str) -> Chat | None:
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM chats WHERE id = ?", (chat_id,))
        return Chat(**dict(rows[0])) if rows else None

    async def rename(self, chat_id: str, title: str) -> None:
        async with self._db() as db:
            await db.execute(
                "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
                (title, time.time(), chat_id),
            )
            await db.commit()

    async def delete(self, chat_id: str) -> None:
        async with self._db() as db:
            await db.execute("DELETE FROM events WHERE chat_id = ?", (chat_id,))
            await db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            await db.commit()

    async def append(self, chat_id: str, turn_id: str, event: Event) -> None:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE chat_id = ?",
                (chat_id,),
            )
            await db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    chat_id,
                    turn_id,
                    rows[0]["next"],
                    event.type,
                    json.dumps(event.data, default=str),
                    event.parent_id,
                    event.depth,
                ),
            )
            await db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (time.time(), chat_id))
            await db.commit()

    async def events(self, chat_id: str) -> list[dict]:
        """The whole transcript, in order, ready to send to the browser."""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM events WHERE chat_id = ? ORDER BY seq", (chat_id,)
            )
        return [
            {
                "id": r["id"],
                "turn_id": r["turn_id"],
                "seq": r["seq"],
                "type": r["type"],
                "data": json.loads(r["data"]),
                "parent_id": r["parent_id"],
                "depth": r["depth"],
            }
            for r in rows
        ]

    async def replay_session(self, chat_id: str) -> tuple[list[QueryRecord], list[dict]]:
        """Rebuild a Session's provenance from the log.

        `explain_last_query` reads `session.executed[-1]`. Seeding it from the log is
        what makes "show me the SQL for that" work on turn three, and after a restart.
        """
        executed: list[QueryRecord] = []
        rejected: list[dict] = []
        for event in await self.events(chat_id):
            data = event["data"]
            if event["type"] != "tool_end" or data.get("tool") != "run_query":
                continue
            if data.get("ok"):
                executed.append(
                    QueryRecord(
                        query=data.get("query", {}),
                        sql=data.get("sql"),
                        row_count=data.get("rows_total", 0),
                        duration_ms=data.get("duration_ms"),
                        warnings=data.get("warnings", []),
                    )
                )
            else:
                rejected.append({"query": data.get("query", {}), "errors": data.get("errors", [])})
        return executed, rejected


@asynccontextmanager
async def checkpointer(path: Path | None = None):
    """LangGraph's thread state. Same file as the transcript, different tables."""
    target = path or settings.db_path
    target.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(target)) as saver:
        yield saver
