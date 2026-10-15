"""What chat management sends and receives.

`Chat` is `transcript.Chat` used directly — a tier-0 dataclass of six primitive fields that is
already the browser's `Chat`. A copy here would be a second definition of one shape with nothing
asserting the two agree.
"""

from pydantic import BaseModel


class NewChat(BaseModel):
    """A chat starts with a placeholder title; the first question replaces it."""

    title: str = "New chat"


class Rename(BaseModel):
    title: str


class Ok(BaseModel):
    """A write that has nothing to report but that it happened."""

    ok: bool = True
