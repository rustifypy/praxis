"""Chat history and the LangGraph checkpointer in one SQLite file. The event log *is* the
transcript, so provenance survives a restart rather than dying with a per-request Session.
"""

import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ..agent.tools import QueryRecord
from ..config import settings
from ..db import SCHEMA, connect
from .events import Event


@dataclass
class Chat:
    id: str
    title: str
    owner_id: str
    source_name: str
    kind: str
    """'question' or 'model' — which agent this chat runs."""
    branch: str
    created_at: float
    updated_at: float


class ChatStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.db_path

    def _db(self):
        return connect(self.path)

    async def init(self) -> None:
        async with self._db() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def create(
        self,
        title: str = "New chat",
        owner_id: str = "local",
        source_name: str = "",
        kind: str = "question",
        branch: str = "",
    ) -> Chat:
        now = time.time()
        chat = Chat(
            id=uuid.uuid4().hex,
            title=title,
            owner_id=owner_id,
            source_name=source_name,
            kind=kind,
            branch=branch,
            created_at=now,
            updated_at=now,
        )
        async with self._db() as db:
            # Named, not positional: a column added to the schema would otherwise shift
            # every value one place to the left without erroring.
            await db.execute(
                "INSERT INTO chats (id, title, owner_id, source_name, kind, branch,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat.id,
                    chat.title,
                    chat.owner_id,
                    chat.source_name,
                    chat.kind,
                    chat.branch,
                    chat.created_at,
                    chat.updated_at,
                ),
            )
            await db.commit()
        return chat

    # Not `list` — a method of that name shadows the builtin for every annotation
    # later in the class body, and `list[dict]` then fails at import time.
    async def list_chats(self, owner_id: str = "local") -> list[Chat]:
        """The question chats. Model-editor chats are excluded — they belong to a source, and
        are listed beside the branch they edit rather than in the chat sidebar."""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM chats WHERE owner_id = ? AND kind = 'question'"
                " ORDER BY updated_at DESC",
                (owner_id,),
            )
        return [Chat(**dict(r)) for r in rows]

    async def create_model_chat(self, source_name: str, branch: str, owner_id: str = "local"):
        """A chat that edits `source_name`'s model."""
        return await self.create(f"Editing {branch}", owner_id, source_name, "model", branch)

    async def model_chats(self, source_name: str) -> list[dict]:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT id, title, updated_at, branch FROM chats"
                " WHERE source_name = ? AND kind = 'model' ORDER BY updated_at DESC",
                (source_name,),
            )
        return [dict(r) for r in rows]

    async def model_chat_source(self, chat_id: str) -> str:
        """Which source this chat edits, or "" when it is a question chat."""
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT source_name FROM chats WHERE id = ? AND kind = 'model'", (chat_id,)
            )
        return rows[0]["source_name"] if rows else ""

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
            await db.execute("DELETE FROM results WHERE chat_id = ?", (chat_id,))
            await db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            await db.commit()

    async def append(self, chat_id: str, turn_id: str, event: Event) -> None:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE chat_id = ?",
                (chat_id,),
            )
            await db.execute(
                "INSERT INTO events (id, chat_id, turn_id, seq, type, data, parent_id, depth)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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

    async def save_result(
        self, chat_id: str, turn_id: str, columns: list[dict], rows: list[dict]
    ) -> str:
        """Keep a whole result set and return the handle the browser fetches it by."""
        result_id = uuid.uuid4().hex
        async with self._db() as db:
            await db.execute(
                "INSERT INTO results (id, chat_id, turn_id, columns, data, row_count, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    chat_id,
                    turn_id,
                    json.dumps(columns, default=str),
                    json.dumps(rows, default=str),
                    len(rows),
                    time.time(),
                ),
            )
            await db.commit()
        return result_id

    async def result(self, result_id: str) -> dict | None:
        """One stored result, whole; paging is the caller's slice. The rows are a single JSON blob,
        so no SQL could return page two on its own.
        """
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM results WHERE id = ?", (result_id,))
        if not rows:
            return None
        record = rows[0]
        return {
            "id": record["id"],
            "chat_id": record["chat_id"],
            "turn_id": record["turn_id"],
            "columns": json.loads(record["columns"]),
            "rows": json.loads(record["data"]),
            "row_count": record["row_count"],
            "created_at": record["created_at"],
        }

    async def replay_session(self, chat_id: str) -> tuple[list[QueryRecord], list[dict]]:
        """Rebuild a Session's provenance from the log, so `explain_last_query` still answers on
        turn three and after a restart.
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
                        # Absent on events written before results had a header; `set_chart`
                        # then rejects for want of columns rather than raising.
                        columns=data.get("columns", []),
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
