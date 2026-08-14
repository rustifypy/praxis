"""One turn of a chat, as a stream of events. Tool events arrive already authored by the
tools, so only token deltas are interpreted here — and only to split reasoning from text.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessageChunk

from ..core.config import settings
from ..core.events import Event
from ..core.llm import reasoning_of, text_of
from ..cube.client import ADMIN_TIMEOUT_S, CubeClient
from ..dashboards.store import DashboardStore
from ..sources import vcs
from ..sources.grounding import for_active_source
from ..sources.registry import Source
from ..transcript import ChatStore
from .build import (
    COMPACTION_TAG,
    build_agent,
    build_editor_agent,
)
from .session import Session, replay
from .tools.dashboards import DashboardContext
from .tools.editor import EditContext

Build = Callable[[str], Awaitable[Any]]
"""Given this turn's id, the agent to run. A callable rather than an agent so that building
one — which reads a catalog over HTTP — fails into the transcript like any other error."""


async def run_turn(
    build: Build,
    chat_id: str,
    question: str,
    store: ChatStore,
    cancel: asyncio.Event | None = None,
    files: list[str] | None = None,
) -> AsyncIterator[Event]:
    """One agent's stream as events, stored as they arrive. Shared by the query chat and the
    model editor: what may reach a transcript is subtle, and two copies would drift.

    `files` are workspace paths the question arrived with. They ride on the user event rather than
    an event of their own: an upload happens between turns and would have no turn to belong to,
    and this is the turn that used it.
    """
    turn_id = uuid.uuid4().hex
    user_event = Event(type="user", data={"text": question, "files": files or []})
    await store.append(chat_id, turn_id, user_event)
    yield user_event

    buffer = {"thinking": "", "text": ""}

    async def flush() -> None:
        """Reasoning precedes answer text within a model call, so draining in this order keeps the
        stored order faithful to the live one.
        """
        for kind in ("thinking", "text"):
            if buffer[kind]:
                block = Event(type=kind, data={"text": buffer[kind]})
                buffer[kind] = ""
                await store.append(chat_id, turn_id, block)

    try:
        agent = await build(turn_id)
        async for mode, payload in agent.astream(
            {"messages": [{"role": "user", "content": question}]},
            config={"configurable": {"thread_id": chat_id}},
            stream_mode=["custom", "messages"],
        ):
            if cancel and cancel.is_set():
                break

            if mode == "custom":
                # Flush first: the thinking that led to a tool call belongs above it.
                await flush()
                event = Event(**payload)
                await store.append(chat_id, turn_id, event)
                yield event
                continue

            chunk, meta = payload
            # Two things must not reach the transcript as the agent's answer: tool
            # return values (ToolMessage chunks flow through this same stream, and
            # run_query's is a JSON row dump) and the summarizing model's output.
            if not isinstance(chunk, AIMessageChunk):
                continue
            if COMPACTION_TAG in (meta.get("tags") or []):
                continue
            if thinking := reasoning_of(chunk.content):
                buffer["thinking"] += thinking
                yield Event(type="thinking", data={"delta": thinking})
            if text := text_of(chunk.content):
                buffer["text"] += text
                yield Event(type="text", data={"delta": text})

        await flush()
    except Exception as exc:  # noqa: BLE001 — a failed turn is a transcript entry, not a 500
        await flush()
        event = Event(type="error", data={"message": str(exc)})
        await store.append(chat_id, turn_id, event)
        yield event

    yield Event(type="done", data={"turn_id": turn_id})


async def stream_turn(
    chat_id: str,
    question: str,
    store: ChatStore,
    saver,
    cube: CubeClient,
    cancel: asyncio.Event | None = None,
    files: list[str] | None = None,
) -> AsyncIterator[Event]:
    """`cube` is the app's session for the active source — borrowed, never closed here. It
    re-mints its own token, so a chat outliving the 15-minute JWT no longer 403s mid-thought."""

    async def build(turn_id: str):
        session = Session(
            catalog=await cube.catalog(),
            client=cube,
            grounding=await for_active_source(),
            max_rows=settings.max_rows,
            # Result sets are kept beside the transcript, under this turn, so the table
            # and the CSV have something to address after the stream has closed.
            store=store,
            chat_id=chat_id,
            turn_id=turn_id,
        )
        # Seeded from the log so `explain_last_query` still answers "show me the SQL for
        # that" on turn three, and after a restart.
        session.executed, session.rejected = replay(await store.events(chat_id))

        # The tools that author dashboards. `grounding.source` is the active source's name,
        # already resolved by `for_active_source` — one lookup, and a new dashboard binds to
        # the same source the answers in this chat came from.
        return build_agent(
            session,
            checkpointer=saver,
            dashboards=DashboardContext(
                store=DashboardStore(),
                catalog=session.catalog,
                source_name=session.grounding.source,
            ),
        )

    async for event in run_turn(build, chat_id, question, store, cancel, files):
        yield event


async def stream_edit_turn(
    chat_id: str,
    question: str,
    source: Source,
    store: ChatStore,
    saver,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[Event]:
    """One turn of a model-editor chat. Its Cube session is pinned to this source's own URL —
    the query agent's follows CUBE_URL, and the source being edited may not be that one."""

    async with CubeClient.for_source(source, timeout=ADMIN_TIMEOUT_S) as client:

        async def build(_turn_id: str):
            # Read per turn, not stored: the branch is whichever one is checked out, because
            # that is the one being served, and the page can switch it between turns.
            branch = await vcs.current(source.model_dir())
            return build_editor_agent(
                EditContext(source=source, client=client, dashboards=DashboardStore()),
                source.name,
                branch,
                checkpointer=saver,
            )

        async for event in run_turn(build, chat_id, question, store, cancel):
            yield event
