"""What a chat in use sends and receives: a turn, a file, a page of rows.

The shapes for making and naming a chat are `chat_management/dto.py`.

**There is no response model for the event log, deliberately.** `GET /{chat_id}/events` returns
`list[dict]`, because the events are a *union* of a dozen types whose one definition is
`core/events.py` on this side and `events.ts`/`applyEvent` on the other. Naming them again here
would be a third definition of the wire format — and the one most likely to drift, since a new
event type would keep working without it and start failing with it.
"""

from typing import Literal

from pydantic import BaseModel

from ...modelstore.shape import Column


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


class Stopped(BaseModel):
    """Whether a turn was in flight to stop. False is normal: the button can land after the turn
    ended, and that must not read as an error."""

    stopped: bool


class Decided(BaseModel):
    """The decision as it was recorded, echoed so the browser renders what the server stored."""

    decision: str


# --- the files beside a chat -------------------------------------------------


class Uploaded(BaseModel):
    """What one upload landed as, profiled by the service on arrival.

    `rows` is 0 and `note` says why when the shape could not be read — **an upload with no profile
    is still an upload**. Reporting it as broken because its summary was malformed is the worst of
    both answers, since the bytes did arrive.
    """

    path: str
    bytes: int
    rows: int
    columns: int
    note: str


class FilePreview(BaseModel):
    """The head of a workspace file, parsed far enough to render.

    `kind` is what the browser branches on: `table` for something that parsed as delimited text,
    `text` for anything readable, `none` for bytes with no useful head — a parquet, a zip, or a
    file whose extension lied.

    Columns are names only. These bytes never went through Cube, so there is no `Column` to say a
    column is money, and a guess dressed as metadata is worse than plain strings.

    `bytes` is the whole file's size, not the head's, and `truncated` says the rest is still there.
    """

    kind: str
    columns: list[str]
    rows: list[list[str]]
    text: str
    bytes: int
    truncated: bool


class Discarded(BaseModel):
    """The path that was removed, echoed so the composer can drop the right chip."""

    deleted: str


# --- the rows a turn produced -----------------------------------------------


class ResultPage(BaseModel):
    """One slice of a stored result set, and the size of the whole.

    `row_count` is the count of the *set*, not of `rows` — the browser draws "showing 200 of
    4,812" from the pair, and a run past the retention depth keeps its count after its rows are
    pruned. `offset` is echoed because the request that asked for it is not the one rendering it.
    """

    id: str
    columns: list[Column]
    rows: list[dict]
    row_count: int
    offset: int
