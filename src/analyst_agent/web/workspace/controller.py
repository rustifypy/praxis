"""Handing a file to the sandbox, reading one back, and parsing a head of it.

**Every function here raises `SandboxError` and never an `HTTPException`.** The mapping to a status
lives at the app boundary — `server.py` registers one handler, because the service supplies its own
status and four routes were otherwise repeating the same three lines. The path guard is the
service's too: it owns the directory, so a refusal from over there is the answer rather than a
second opinion.

The parsing here exists because the browser cannot do it: the head has to be decided on before the
bytes are sent, or a 4 GB CSV becomes a 4 GB response.
"""

import csv
import io
import itertools
from pathlib import Path

from ...sandbox import Profile, open_sandbox
from .dto import Discarded, FilePreview, Uploaded

# Nothing bounds the sandbox's volume, so a 4 GB CSV is silent disk consumption on whoever's
# machine it runs on.
MAX_BYTES = 100 * 1024 * 1024
TOO_BIG = f"that file is larger than the {MAX_BYTES // 1024 // 1024} MB limit"
CHUNK = 1024 * 1024

# What `preview` reads back as a table and as text. Anything else — a parquet, a zip — has no
# useful head, and offering one would render bytes as mojibake.
TABULAR = {".csv", ".tsv"}
READABLE = {".json", ".txt", ".md", ".log", ".py", ".sql", ".yml", ".yaml"}

# The head is read rather than the whole file streamed: `truncated` says the rest is still there.
PREVIEW_ROWS = 50
PREVIEW_BYTES = 256 * 1024


async def store(chat_id: str, filename: str, body: bytes) -> Uploaded:
    """Hand the bytes over and report the shape the service read.

    The filename is a browser's, so the *service* turns it into a path — one place, checked on the
    resolved target, on the side that owns the directory.

    Profiled on arrival by the service, so this is one round trip rather than two. A shape it could
    not read is a note and never a failure.
    """
    async with await open_sandbox() as sandbox:
        answer = await sandbox.upload(chat_id, filename, body)
    try:
        read = Profile.model_validate(answer.get("profile") or {"path": answer["path"]})
    except ValueError:
        read = Profile(path=answer["path"], note="not profiled")
    return Uploaded(
        path=answer["path"],
        bytes=read.bytes,
        rows=read.rows,
        columns=len(read.columns),
        note=read.note,
    )


async def read_bytes(chat_id: str, path: str) -> bytes:
    """One file's bytes. The path guard is the service's — see the module docstring."""
    async with await open_sandbox() as sandbox:
        data, _ = await sandbox.read(chat_id, path)
    return data


async def preview(chat_id: str, path: str) -> FilePreview:
    """The head of a file, as a table when it parses as one and as text when it does not.

    A file with no useful head answers `kind: "none"` and is never read at all — the extension
    decides that before a request reaches the service.
    """
    suffix = Path(path).suffix.lower()
    if suffix not in TABULAR and suffix not in READABLE:
        return FilePreview(kind="none", columns=[], rows=[], text="", bytes=0, truncated=False)

    whole = await read_bytes(chat_id, path)
    size = len(whole)
    # Read whole and sliced, rather than the first N bytes off a handle: the file is on the far
    # side of an HTTP call now, and `MAX_BYTES` already bounds what can be there.
    head = whole[:PREVIEW_BYTES]
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


async def discard(chat_id: str, path: str) -> Discarded:
    """Delete the file, not just the chip: `uploads/` is what the prompt is built from every turn,
    so a chip removed while the file stayed would keep describing it."""
    async with await open_sandbox() as sandbox:
        await sandbox.delete(chat_id, path)
    return Discarded(deleted=path)
