import uuid
from typing import Any, Literal

from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

EventType = Literal[
    "user",
    "thinking",
    "text",
    "tool_start",
    "output",
    "approval",
    "tool_end",
    "compacted",
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
    try:
        writer = get_stream_writer()
        writer(event.model_dump())
    except (RuntimeError, KeyError):
        return
