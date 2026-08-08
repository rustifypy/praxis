"""Server-sent events, framed once.

Both streams in the portal — a chat turn and a job — are the same wire format, and the
headers are the load-bearing part: without them a proxy or the browser sits on the
stream and the whole thing arrives in one lump at the end.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel


def sse(events: AsyncIterator[Any]) -> StreamingResponse:
    """Frame an async iterator of JSON-serialisable events as `text/event-stream`.

    Accepts pydantic models and dataclass-ish objects: the chat yields `Event` models,
    a job yields plain dataclasses.
    """

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
