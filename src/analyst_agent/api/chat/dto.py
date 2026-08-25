"""What a chat sends and receives.

`Chat` is `transcript.Chat` used directly — a tier-1 dataclass of six primitive fields that is
already the browser's `Chat`. A copy here would be a second definition of one shape with nothing
asserting the two agree.

**There is no response model for the event log, deliberately.** `GET /{chat_id}/events` returns
`list[dict]`, because the events are a *union* of a dozen types whose one definition is
`core/events.py` on this side and `events.ts`/`applyEvent` on the other. Naming them again here
would be a third definition of the wire format — and the one most likely to drift, since a new
event type would keep working without it and start failing with it.
"""

from typing import Literal

from pydantic import BaseModel


class NewChat(BaseModel):
    """A chat starts with a placeholder title; the first question replaces it."""

    title: str = "New chat"


class Rename(BaseModel):
    title: str


class Decision(BaseModel):
    """The answer to a script waiting for approval.

    A `Literal` and not a string, so a typo is a 422 from the schema rather than something that
    falls through a comparison and reads as "yes".
    """

    decision: Literal["yes", "always", "no"] = "yes"


class Question(BaseModel):
    """One message, plus the workspace files it should be told about.

    `files` is filtered against what the sandbox actually holds before the agent sees it — a path
    the browser names and the service does not have is dropped rather than refused.
    """

    question: str
    files: list[str] = []


class Ok(BaseModel):
    """A write that has nothing to report but that it happened."""

    ok: bool = True


class Stopped(BaseModel):
    """Whether a turn was in flight to stop. False is normal: the button can land after the turn
    ended, and that must not read as an error."""

    stopped: bool


class Decided(BaseModel):
    """The decision as it was recorded, echoed so the browser renders what the server stored."""

    decision: str
