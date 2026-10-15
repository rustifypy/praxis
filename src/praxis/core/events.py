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
    "proposal",
    "tool_end",
    "compacted",
    "error",
    "done",
]
"""The wire format between agent and browser, and the transcript's own vocabulary.

Two of these carry a body, and both are exceptions worth knowing about:

- `approval` carries the script about to run, because the moment before a run is the only one
  where the script is readable. It is not stored — the file is in the sandbox's workspace and
  the outcome is on `tool_end`.
- `proposal` carries a model file the editor agent wants to write. It *is* stored, because
  nothing else holds it: the agent commits nothing, so there is no ref and no file on disk to
  point a replayed transcript at.

`output` is the one type that is never stored, because `tool_end` already carries the whole of a
script's output trimmed. Any other wire-only type would be state that exists nowhere.
"""


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
