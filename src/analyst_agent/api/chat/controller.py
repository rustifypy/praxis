"""One turn, the files beside it, and the rows it produced.

`prepare` is the only interesting one: it does everything a turn needs *before* the stream opens,
because once `turn_stream` has the generator there is nowhere left to raise. What it returns is
the three things the agent has to be handed — the Cube session, the attachments that really exist,
and an approval queue if scripts are being approved.

No `HTTPException` here: an unknown chat comes back as `None` and the route decides that is a 404.
"""

import asyncio
import contextlib
import csv
import io
import itertools
from pathlib import Path

from ...core import transcript
from ...core.config import settings
from ...modelstore.shape import Column, export_value
from ...sandbox import Profile, SandboxError, open_sandbox
from ..support import state
from .dto import Decided, Discarded, FilePreview, ResultPage, Stopped, Uploaded


async def events(chat_id: str) -> list[dict]:
    """The event log, verbatim. It *is* the transcript — see the package docstring."""
    return await transcript.events(chat_id)


def stop(app, chat_id: str) -> Stopped:
    """Set the cancel flag for a turn in flight, if there is one."""
    cancel = app.state.cancels.get(chat_id)
    if cancel:
        cancel.set()
    return Stopped(stopped=bool(cancel))


def approve(app, chat_id: str, decision: str) -> Decided | None:
    """Answer the script this chat is waiting on, or `None` if nothing is waiting.

    `always` is remembered on `app.state` rather than stored: a restart is a fair moment to start
    asking again.
    """
    queue = app.state.approvals.get(chat_id)
    if queue is None:
        return None
    if decision == "always":
        app.state.always.add(chat_id)
    queue.put_nowait(decision)
    return Decided(decision=decision)


async def prepare(app, chat_id: str, question: str, files: list[str]):
    """Everything a turn needs before its stream opens, or `None` if there is no such chat.

    Returns `(cube, attached, approvals)`. Done here rather than inside the stream because a turn
    that cannot start must fail as a status code — once the generator is handed to `turn_stream`
    there is no way left to report anything but an event, and a refused turn would read as a
    silent one.

    The first question becomes the title, so a chat list is readable without opening anything.

    Attachments are intersected with what the sandbox actually holds: the prompt is built from that
    directory every turn, so naming a file that is not there would describe one the agent cannot
    open.
    """
    chat = await transcript.get(chat_id)
    if chat is None:
        return None
    if chat.title == "New chat":
        await transcript.rename(chat_id, question[:60])

    cube = await state.cube(app)

    known: list[str] = []
    with contextlib.suppress(SandboxError):
        async with await open_sandbox() as sandbox:
            known, _ = await sandbox.listing(chat_id)
    attached = [path for path in files if path in known]

    approvals = None
    if settings.approve_scripts and chat_id not in app.state.always:
        approvals = asyncio.Queue()
        app.state.approvals[chat_id] = approvals

    return cube, attached, approvals


# --- the files beside a chat -------------------------------------------------

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


# --- the rows a turn produced -----------------------------------------------


async def page(result_id: str, offset: int, limit: int) -> ResultPage | None:
    """One page of a result set, or `None` when no result has that id.

    Sliced here rather than in SQL: the rows are stored as a single JSON blob, so there is no
    query that could return page two on its own. Which means `limit` bounds what is *serialised*,
    not what is read — the whole set is in memory either way.
    """
    record = await transcript.result(result_id)
    if record is None:
        return None
    return ResultPage(
        id=record["id"],
        columns=record["columns"],
        rows=record["rows"][offset : offset + limit],
        row_count=record["row_count"],
        offset=offset,
    )


async def as_csv(result_id: str) -> str | None:
    """The whole result set as CSV text, or `None` when no result has that id.

    Labels in the header and not member ids, which is the one thing this does not share with the
    agent's `export_result` — that writes ids, because a script refers to `sales.invoice_total`
    and a person reads "Invoice total".

    Never paged: a spreadsheet of the first 200 rows silently answers a different question than
    the one that was asked.
    """
    record = await transcript.result(result_id)
    if record is None:
        return None
    columns = [Column(**raw) for raw in record["columns"]]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([column.label for column in columns])
    for row in record["rows"]:
        writer.writerow([export_value(row.get(column.id), column) for column in columns])
    return buffer.getvalue()
