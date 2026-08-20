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
from ..cube.client import ADMIN_TIMEOUT_S, CubeClient, open_client
from ..dashboards.store import DashboardStore
from ..datamodel import grounding, vcs
from ..datamodel.registry import model_dir
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
            if mode == "custom":
                # Flush first: the thinking that led to a tool call belongs above it.
                await flush()
                event = Event(**payload)
                # The two wire-only types. `tool_end` carries the whole of a script's output,
                # trimmed, and the workspace holds the script an approval is asking about — so
                # neither is state that would exist nowhere else.
                if event.type not in ("output", "approval"):
                    await store.append(chat_id, turn_id, event)
                yield event
            else:
                chunk, meta = payload
                # Two things must not reach the transcript as the agent's answer: tool
                # return values (ToolMessage chunks flow through this same stream, and
                # run_query's is a JSON row dump) and the summarizing model's output.
                tagged = COMPACTION_TAG in (meta.get("tags") or [])
                if isinstance(chunk, AIMessageChunk) and not tagged:
                    if thinking := reasoning_of(chunk.content):
                        buffer["thinking"] += thinking
                        yield Event(type="thinking", data={"delta": thinking})
                    if text := text_of(chunk.content):
                        buffer["text"] += text
                        yield Event(type="text", data={"delta": text})

            # After the event and not before it: a stopped script emits its own `tool_end`, and
            # breaking first dropped it — the turn replayed as a step that never finished.
            if cancel and cancel.is_set():
                break

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
    approvals: asyncio.Queue | None = None,
) -> AsyncIterator[Event]:
    """`cube` is the app's session — borrowed, never closed here. It re-mints its own token,
    so a chat outliving the 15-minute JWT no longer 403s mid-thought."""

    async def build(turn_id: str):
        session = Session(
            catalog=await cube.catalog(),
            client=cube,
            # Pinned to main: a question chat and a dashboard must never silently read a branch.
            grounding=await grounding.load(),
            max_rows=settings.max_rows,
            # Result sets are kept beside the transcript, under this turn, so the table
            # and the CSV have something to address after the stream has closed.
            store=store,
            chat_id=chat_id,
            turn_id=turn_id,
            cancel=cancel,
            approvals=approvals,
        )
        # Seeded from the log so `explain_last_query` still answers "show me the SQL for
        # that" on turn three, and after a restart.
        session.executed, session.rejected = replay(await store.events(chat_id))

        return await build_agent(
            session,
            checkpointer=saver,
            dashboards=DashboardContext(store=DashboardStore(), catalog=session.catalog),
        )

    async for event in run_turn(build, chat_id, question, store, cancel, files):
        yield event


async def stream_edit_turn(
    chat_id: str,
    question: str,
    store: ChatStore,
    saver,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[Event]:
    """One turn of a model-editor chat. Its own Cube session, on the admin timeout: an editor
    turn restarts the container and waits for a compile, which a query timeout is not sized for.
    """

    async with await open_client(timeout=ADMIN_TIMEOUT_S) as client:

        async def build(_turn_id: str):
            # Read per turn, not stored: the branch is whichever one is checked out, because
            # that is the one being served, and the page can switch it between turns.
            branch = await vcs.current(model_dir())
            return build_editor_agent(
                EditContext(client=client, dashboards=DashboardStore()),
                branch,
                checkpointer=saver,
            )

        async for event in run_turn(build, chat_id, question, store, cancel):
            yield event
