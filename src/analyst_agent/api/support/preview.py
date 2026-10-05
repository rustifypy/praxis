"""The head of a file, parsed far enough to render — shared by a chat's workspace files and a
Run's outputs.

One parser for both because the hard parts are the same and were learned once: the header line
alone picks the delimiter, a NUL byte means the extension lied, a read that stops mid-line drops
the half row. Where the bytes come from is the caller's business — the sandbox over HTTP for a
chat, the Run's folder on disk for a Flow — so this takes bytes and a size, and reads nothing.
"""

import csv
import io
import itertools
from pathlib import Path

from pydantic import BaseModel

# What `preview` reads back as a table and as text. Anything else — a parquet, a zip — has no
# useful head, and offering one would render bytes as mojibake.
TABULAR = {".csv", ".tsv"}
READABLE = {".json", ".txt", ".md", ".log", ".py", ".sql", ".yml", ".yaml"}

# The head is read rather than the whole file streamed: `truncated` says the rest is still there.
PREVIEW_ROWS = 50
PREVIEW_BYTES = 256 * 1024


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
    # Every data row in the file, not only the head — counted by a caller that can afford to read
    # the whole file. `None` where nobody counted: a chat's preview reads only the head.
    rows_total: int | None = None


NONE = FilePreview(kind="none", columns=[], rows=[], text="", bytes=0, truncated=False)


def readable(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in TABULAR or suffix in READABLE


def parse(path: str, head: bytes, size: int) -> FilePreview:
    """`head` is the first `PREVIEW_BYTES` of the file at most, and `size` the whole file's."""
    suffix = Path(path).suffix.lower()
    clipped = size > len(head)
    # `replace` rather than a guessed encoding: a file the sandbox wrote is UTF-8 and one the user
    # uploaded may be anything, and a few replacement characters beat a 500.
    text = head.decode("utf-8", "replace")
    # The extension is a claim, not a fact. A NUL byte is what actually separates a text file from
    # a parquet somebody named `.csv` — without this, `csv.reader` is happy to report the first 40
    # bytes of it as a column name.
    if "\x00" in text:
        return FilePreview(kind="none", columns=[], rows=[], text="", bytes=size, truncated=False)
    if clipped:
        # The read stops mid-line, and half a row parsed is a row that states something false.
        text = text[: text.rfind("\n") + 1]

    columns: list[str] = []
    rows: list[list[str]] = []
    more = False
    if suffix in TABULAR:
        # The header line alone decides the delimiter. `csv.Sniffer` guesses from frequency across
        # a whole sample, which finds the comma inside one prose field and calls a one-column file
        # twelve.
        delimiter = "\t" if suffix == ".tsv" else max(",;\t|", key=text.split("\n", 1)[0].count)
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        try:
            parsed = list(itertools.islice(reader, PREVIEW_ROWS + 1))
            more = next(reader, None) is not None
        except csv.Error:
            # A NUL byte or an oversized field — not really a CSV. Falling through to text is how
            # you find out it is the HTML of a failed download.
            parsed = []
        if parsed:
            # Lists and not objects: a repeated header name would silently drop a column.
            columns = [name or f"column {index + 1}" for index, name in enumerate(parsed[0])]
            # Ragged rows are normal in a file a script appended to. Padded rather than dropped, so
            # the table stays rectangular and the gap shows as an empty cell.
            rows = [row[: len(columns)] + [""] * (len(columns) - len(row)) for row in parsed[1:]]

    if columns:
        return FilePreview(
            kind="table", columns=columns, rows=rows, text="", bytes=size, truncated=clipped or more
        )
    return FilePreview(kind="text", columns=[], rows=[], text=text, bytes=size, truncated=clipped)
