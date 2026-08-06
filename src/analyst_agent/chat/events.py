"""The wire format between the agent and the browser.

One flat event stream. Deltas go over the wire so the transcript fills as the agent
works; the store coalesces them into one row per block so replaying a chat is cheap.

`parent_id` and `depth` are reserved and always None/0 today. When the orchestrator and
its subagents land, nested events fill them in and the renderer indents by depth — the
schema does not change. Reserving two fields now is cheaper than migrating both ends.
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
    """Push an event from inside a tool into the stream.

    LangGraph's writer is a no-op when nobody is streaming, so `analyst ask` is
    unaffected. It raises only when there is no runnable context at all, which is how
    unit tests invoke a tool directly — swallow that rather than making tools
    untestable outside a graph.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer(event.model_dump())
