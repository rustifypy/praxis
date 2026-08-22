"""One wire format for both streams: a chat turn and a job's narration.

`data: {json}\n\n` and the two headers that stop a proxy buffering it. Shared because the
framing is not a per-caller decision, and a second copy is one that would eventually disagree
about the blank line.

`turn_stream` is the same argument one layer up: the two chats differ in which agent they open
and in nothing else, so the cancel flag's registration and — whichever way the stream ends — its
removal live here rather than being written out beside each of them.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.events import Event


def sse(events: AsyncIterator[Any]) -> StreamingResponse:
    async def frames():
        async for event in events:
            body = (
                event.model_dump_json()
                if isinstance(event, BaseModel)
                else json.dumps(getattr(event, "__dict__", event), default=str)
            )
            yield f"data: {body}\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def turn_stream(
    request: Request,
    chat_id: str,
    open_turn: Callable[[asyncio.Event], AsyncIterator[Event]],
) -> StreamingResponse:
    """One turn as SSE, with the cancel flag registered around it.

    A closed tab must not leave the agent burning model calls — nor a script waiting forever on
    an approval nobody is there to give. The approval queue is dropped for both chats: only the
    query agent ever sets one, and popping a key that was never there is what keeps this single.
    """
    cancel = asyncio.Event()
    request.app.state.cancels[chat_id] = cancel

    async def events():
        try:
            async for event in open_turn(cancel):
                yield event
                if await request.is_disconnected():
                    cancel.set()
        finally:
            request.app.state.cancels.pop(chat_id, None)
            request.app.state.approvals.pop(chat_id, None)

    return sse(events())
