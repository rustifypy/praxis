"""One turn of a chat, as a stream of events.

Tool events arrive already authored — the tools write them through LangGraph's stream
writer, so a gate rejection reaches the browser with its reasons intact instead of
being inferred from a tool-call chunk. Only token deltas need interpreting here, and
only to separate reasoning from answer text.

Deltas go over the wire; whole blocks go to the store.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk

from ..agent.build import COMPACTION_TAG, SecurityContext, build_agent, load_catalog
from ..agent.content import reasoning_of, text_of
from ..agent.tools import Session
from ..config import settings
from ..cube.client import CubeClient, mint_token
from ..grounding import for_active_source
from .events import Event
from .store import ChatStore


async def stream_turn(
    chat_id: str,
    question: str,
    store: ChatStore,
    saver,
    ctx: SecurityContext | None = None,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[Event]:
    ctx = ctx or SecurityContext(user_id="local")
    turn_id = uuid.uuid4().hex

    user_event = Event(type="user", data={"text": question})
    await store.append(chat_id, turn_id, user_event)
    yield user_event

    # Minted per turn. The Cube JWT lives 15 minutes and a chat outlives that easily,
    # so a client cached for the conversation would start 403ing mid-thought.
    client = CubeClient(mint_token(ctx.as_claims()))
    buffer = {"thinking": "", "text": ""}

    async def flush() -> None:
        """Persist what has streamed so far, in transcript order.

        Reasoning always precedes answer text within a model call, so draining in this
        order is enough to keep the stored order faithful to the live one.
        """
        for kind in ("thinking", "text"):
            if buffer[kind]:
                block = Event(type=kind, data={"text": buffer[kind]})
                buffer[kind] = ""
                await store.append(chat_id, turn_id, block)

    try:
        session = Session(
            catalog=await load_catalog(client),
            client=client,
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
        session.executed, session.rejected = await store.replay_session(chat_id)

        agent = build_agent(session, ctx, checkpointer=saver)
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
    finally:
        await client.aclose()

    yield Event(type="done", data={"turn_id": turn_id})
