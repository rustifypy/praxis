"""The wire format between agent and browser: one flat event stream, deltas over the wire and
whole blocks to the store. `parent_id`/`depth` are reserved for subagents, None/0 today.
"""

import uuid
from typing import Any, Literal

from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

EventType = Literal[
    "user",  # the question, echoed back so replay is a single source
    "thinking",  # reasoning summary delta
    "text",  # answer delta
    "tool_start",  # label + args, emitted by the tool itself
    "output",  # a running tool's partial output — never stored; `tool_end` carries the whole
    "approval",  # a script waiting on a click — never stored; the workspace holds the script
    "tool_end",  # rows, sql, gate errors
    "compacted",  # SummarizationMiddleware rewrote the history
    "error",
    "done",
]


class Event(BaseModel):
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    depth: int = 0
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)


def emit(event: Event) -> None:
    """Push an event from inside a tool into the stream. A no-op outside a graph, where
    `get_stream_writer` raises `RuntimeError` or `KeyError('__pregel_runtime')`.
    """
    try:
        writer = get_stream_writer()
    except (RuntimeError, KeyError):
        return
    writer(event.model_dump())
