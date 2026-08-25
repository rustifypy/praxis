import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessageChunk

from .. import transcript
from ..core.config import settings
from ..core.deployment import MAIN
from ..core.events import Event
from ..core.llm import reasoning_of, text_of
from ..modelstore.client import Bound, open_store
from .build import (
    COMPACTION_TAG,
    build_agent,
    build_editor_agent,
)
from .session import Session, replay
from .tools.dashboards import DashboardContext
from .tools.editor import EditContext

# Given this turn's id, the agent to run. A callable rather than an agent so that building one
# — which reads a catalog over HTTP — fails into the transcript like any other error.
Build = Callable[[str], Awaitable[Any]]


async def run_turn(
    build: Build,
    chat_id: str,
    question: str,
    cancel: asyncio.Event | None = None,
    files: list[str] | None = None,
) -> AsyncIterator[Event]:
    # Shared by the query chat and the model editor: what may reach a transcript is subtle, and
    # two copies would drift. `files` ride on the user event rather than one of their own — an
    # upload happens between turns and would have no turn to belong to.
    turn_id = uuid.uuid4().hex
    buffer = {"thinking": "", "text": ""}

    async def flush() -> None:
        # Reasoning precedes answer text within a model call, so draining in this order keeps
        # the stored order faithful to the live one.
        for kind in ("thinking", "text"):
            if buffer[kind]:
                block = Event(type=kind, data={"text": buffer[kind]})
                buffer[kind] = ""
                await transcript.append(chat_id, turn_id, block)

    # The user event is inside the try as well: a transcript that cannot be written is a failed
    # turn like any other, and the handler below is what turns one into an event rather than an
    # exception escaping into the stream.
    try:
        user_event = Event(type="user", data={"text": question, "files": files or []})
        await transcript.append(chat_id, turn_id, user_event)
        yield user_event

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
                # The two wire-only types: `tool_end` carries the whole of a script's output
                # trimmed, and the workspace holds the script an approval asks about, so
                # neither is state that would exist nowhere else.
                if event.type not in ("output", "approval"):
                    await transcript.append(chat_id, turn_id, event)
                yield event
            else:
                chunk, meta = payload
                # Two things must not reach the transcript as the agent's answer: tool return
                # values (run_query's is a JSON row dump) and the summarizer's output.
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
        await transcript.append(chat_id, turn_id, event)
        yield event

    yield Event(type="done", data={"turn_id": turn_id})


async def stream_turn(
    chat_id: str,
    question: str,
    saver,
    cube: Bound,
    cancel: asyncio.Event | None = None,
    files: list[str] | None = None,
    approvals: asyncio.Queue | None = None,
) -> AsyncIterator[Event]:
    # `cube` is the app's session: borrowed, never closed here.

    async def build(turn_id: str):
        session = Session(
            catalog=await cube.catalog(),
            client=cube,
            # Pinned to main: a question chat and a dashboard must never silently read an edit ref.
            max_rows=settings.max_rows,
            # Result sets live beside the transcript under this turn, so the table and the CSV
            # have something to address after the stream has closed.
            chat_id=chat_id,
            turn_id=turn_id,
            cancel=cancel,
            approvals=approvals,
        )
        # Seeded from the log, so "show me the SQL for that" still answers on turn three.
        session.executed, session.rejected = replay(await transcript.events(chat_id))

        return await build_agent(
            session,
            checkpointer=saver,
            dashboards=DashboardContext(catalog=session.catalog),
        )

    async for event in run_turn(build, chat_id, question, cancel, files):
        yield event


async def stream_edit_turn(
    chat_id: str,
    question: str,
    saver,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[Event]:
    """One editor turn.

    Nothing about the model is passed in: the agent reads the ref's draft, which is where every
    unpublished edit lives. `EditContext.staged` carries only this turn's own proposals, which
    are on no ref and in no file until the user accepts them.
    """
    # The ref is the chat's own, fixed when it was created: an edit chat is a conversation about
    # one ref, and a transcript that followed a page's selection would describe edits to a model
    # it was not editing.
    chat = await transcript.get(chat_id)
    ref = (chat and chat.ref) or MAIN
    # The store and nothing else. There is no Cube session here any more: the one tool that needs
    # Cube is `validate_model`, and it goes through `workspace.verify`, which opens a client on
    # the throwaway ref it compiles — a session pinned to this ref would be the wrong one.
    async with await open_store() as model_store:

        async def build(_turn_id: str):
            return build_editor_agent(
                EditContext(store=model_store, ref=ref),
                ref,
                checkpointer=saver,
            )

        async for event in run_turn(build, chat_id, question, cancel):
            yield event
