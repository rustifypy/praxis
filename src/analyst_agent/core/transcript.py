"""The chats, their events and their results — the transcript, and the checkpointer beside it.

Functions and not a store class: the only state such a class held was a path, every caller in
the app used the default, and no test ever substituted a double for it. Which database this
talks to is `settings.db_path`, in one place.

Storage only. Turning a transcript back into provenance is `agent.session.replay`.
"""

import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from . import db
from .config import settings
from .events import Event

# TODO: this file does not survive the refactor. Not to the chat controller though — `agent/turn.py`
# and the query and code tools all append here, and `agent` sits below `web`. It goes to `core/`,
# beside the other accessors over tables `core.db` already owns.


@dataclass
class Chat:
    id: str
    title: str
    # 'question' or 'model' — which agent this chat runs.
    kind: str
    ref: str
    created_at: float
    updated_at: float


async def create(title: str = "New chat", kind: str = "question", ref: str = "") -> Chat:
    now = time.time()
    chat = Chat(
        id=uuid.uuid4().hex,
        title=title,
        kind=kind,
        ref=ref,
        created_at=now,
        updated_at=now,
    )
    async with db.connect() as conn:
        await conn.execute(sa.insert(db.chats).values(**chat.__dict__))
    return chat


async def list_chats(kind: str = "question") -> list[Chat]:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.chats)
            .where(db.chats.c.kind == kind)
            .order_by(db.chats.c.updated_at.desc())
        )
    return [Chat(**row) for row in found.mappings()]


async def get(chat_id: str) -> Chat | None:
    async with db.connect() as conn:
        found = await conn.execute(sa.select(db.chats).where(db.chats.c.id == chat_id))
    row = found.mappings().first()
    return Chat(**row) if row else None


async def rename(chat_id: str, title: str) -> None:
    async with db.connect() as conn:
        await conn.execute(
            sa.update(db.chats)
            .where(db.chats.c.id == chat_id)
            .values(title=title, updated_at=time.time())
        )


async def delete_chat(chat_id: str) -> None:
    # The children go explicitly rather than by cascade: the checkpointer writes its own tables
    # into this file, and a partial delete would be worse than a slow one.
    async with db.connect() as conn:
        await conn.execute(sa.delete(db.events).where(db.events.c.chat_id == chat_id))
        await conn.execute(sa.delete(db.results).where(db.results.c.chat_id == chat_id))
        await conn.execute(sa.delete(db.chats).where(db.chats.c.id == chat_id))


async def append(chat_id: str, turn_id: str, event: Event) -> None:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(sa.func.coalesce(sa.func.max(db.events.c.seq), 0) + 1).where(
                db.events.c.chat_id == chat_id
            )
        )
        await conn.execute(
            sa.insert(db.events).values(
                id=event.id,
                chat_id=chat_id,
                turn_id=turn_id,
                seq=found.scalar_one(),
                type=event.type,
                data=event.data,
                parent_id=event.parent_id,
                depth=event.depth,
            )
        )
        await conn.execute(
            sa.update(db.chats).where(db.chats.c.id == chat_id).values(updated_at=time.time())
        )


async def events(chat_id: str) -> list[dict]:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.events).where(db.events.c.chat_id == chat_id).order_by(db.events.c.seq)
        )
    # `chat_id` dropped: the caller asked for one chat and already knows which.
    return [
        {key: value for key, value in row.items() if key != "chat_id"} for row in found.mappings()
    ]


async def save_result(chat_id: str, turn_id: str, columns: list[dict], rows: list[dict]) -> str:
    result_id = uuid.uuid4().hex
    async with db.connect() as conn:
        await conn.execute(
            sa.insert(db.results).values(
                id=result_id,
                chat_id=chat_id,
                turn_id=turn_id,
                columns=columns,
                data=rows,
                row_count=len(rows),
                created_at=time.time(),
            )
        )
    return result_id


async def result(result_id: str) -> dict | None:
    # Whole, and paging is the caller's slice: the rows are one JSON blob, so no SQL could
    # return page two on its own.
    async with db.connect() as conn:
        found = await conn.execute(sa.select(db.results).where(db.results.c.id == result_id))
    row = found.mappings().first()
    if row is None:
        return None
    # `data` is the column and `rows` is what every caller reads, because `ROWS` is a SQLite
    # keyword and could not be the column's name.
    return {**{key: value for key, value in row.items() if key != "data"}, "rows": row["data"]}


@asynccontextmanager
async def checkpointer(path: Path | None = None):
    target = path or settings.db_path
    target.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(target)) as saver:
        yield saver
