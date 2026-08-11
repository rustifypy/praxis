"""Server-sent events, framed once."""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel


def sse(events: AsyncIterator[Any]) -> StreamingResponse:
    """Frame an async iterator of JSON-serialisable events as `text/event-stream`."""

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
