"""What the workspace routes answer with.

The two downloads have no model: one is the file's own bytes and the other is a CSV, and neither
is JSON. They are reached as plain links authenticated by the token cookie, not through `fetch`.
"""

from pydantic import BaseModel


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
