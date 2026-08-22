"""The sandbox boundary: a file in, a file out, and taking one back.

Under `/api/chats` because a workspace belongs to a chat, but nothing here touches the
transcript — the files live in the *service's* volume, which is why none of them has a row. A
row could only ever be a second copy of `ls`, and the copy is what would go stale.
"""

import contextlib
import csv
import io
import itertools
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import transcript
from ..sandbox import Profile, SandboxError, open_sandbox

router = APIRouter(prefix="/api/chats")

# Nothing bounds the sandbox's volume, so a 4 GB CSV is silent disk consumption on whoever's
# machine it runs on.
MAX_BYTES = 100 * 1024 * 1024
TOO_BIG = f"that file is larger than the {MAX_BYTES // 1024 // 1024} MB limit"
CHUNK = 1024 * 1024

# Raster only, and the type is derived from the extension rather than echoing the browser's: an
# SVG is a document that can carry script, and serving one inline would hand it this origin.
INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# What `/preview` reads back as a table and as text. Anything else — a parquet, a zip — has no
# useful head, and offering one would render bytes as mojibake.
TABULAR = {".csv", ".tsv"}
READABLE = {".json", ".txt", ".md", ".log", ".py", ".sql", ".yml", ".yaml"}

# The head is read rather than the whole file streamed: `truncated` says the rest is still there.
PREVIEW_ROWS = 50
PREVIEW_BYTES = 256 * 1024


@contextlib.asynccontextmanager
async def _sandbox():
    # What a sandbox failure becomes as HTTP, in one place — 503 when there is no sandbox at all,
    # since a deployment without one is a working deployment that cannot run Python, and the
    # service's own status for anything that happens inside the block.
    try:
        async with await open_sandbox() as sandbox:
            yield sandbox
    except SandboxError as exc:
        # The service's own status when it gave one; 503 when it did not, which is both "no
        # sandbox is configured" and "it did not answer" — a working deployment that cannot
        # run Python either way.
        raise HTTPException(exc.status or 503, str(exc)) from None


@router.post("/{chat_id}/files")
async def upload(chat_id: str, file: UploadFile, request: Request) -> dict:
    """Hand a file to the sandbox and report the shape it read.

    The filename is a browser's, so the service is what turns it into a path — one place,
    checked on the resolved target, on the side that owns the directory.
    """
    if await transcript.get(chat_id) is None:
        raise HTTPException(404, "no such chat")

    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, TOO_BIG)

    # Content-Length is the cheap refusal, not a promise — a chunked upload sends none at all.
    body = bytearray()
    while chunk := await file.read(CHUNK):
        body += chunk
        if len(body) > MAX_BYTES:
            raise HTTPException(413, TOO_BIG)

    async with _sandbox() as sandbox:
        body_json = await sandbox.upload(chat_id, file.filename or "upload", bytes(body))
    # Profiled on arrival by the service, so this is one round trip rather than two. A shape it
    # could not read is a note, never a failure: the file did arrive, and an upload reported as
    # broken because its summary was malformed is the worst of both answers.
    try:
        read = Profile.model_validate(body_json.get("profile") or {"path": body_json["path"]})
    except ValueError:
        read = Profile(path=body_json["path"], note="not profiled")
    return {
        "path": body_json["path"],
        "bytes": read.bytes,
        "rows": read.rows,
        "columns": len(read.columns),
        "note": read.note,
    }


# Below the chat routes that end in a fixed segment, and `{path:path}` because an artifact is
# `charts/q1.png` as readily as `q1.png`.
@router.get("/{chat_id}/files/{path:path}")
async def download(chat_id: str, path: str) -> Response:
    data = await _read(chat_id, path)
    inline = Path(path).suffix.lower() in INLINE
    headers = {"X-Content-Type-Options": "nosniff"}
    if not inline:
        headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
    return Response(
        data,
        media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        headers=headers,
    )


async def _read(chat_id: str, path: str) -> bytes:
    """One file's bytes from the sandbox, or the refusal as a status. The path guard is over
    there, on the side that owns the directory — so a 400 here is its answer, not a second
    opinion."""
    async with _sandbox() as sandbox:
        data, _ = await sandbox.read(chat_id, path)
    return data


@router.get("/{chat_id}/preview/{path:path}")
async def preview(chat_id: str, path: str) -> dict:

    suffix = Path(path).suffix.lower()
    if suffix not in TABULAR and suffix not in READABLE:
        return {
            "columns": [],
            "rows": [],
            "text": "",
            "bytes": 0,
            "truncated": False,
            "kind": "none",
        }

    whole = await _read(chat_id, path)
    size = len(whole)
    empty = {"columns": [], "rows": [], "text": "", "bytes": size, "truncated": False}
    # Read whole and sliced, rather than the first N bytes off a handle: the file is on the far
    # side of an HTTP call now, and `MAX_BYTES` already bounds what can be there.
    head = whole[:PREVIEW_BYTES]
    clipped = size > len(head)
    # `replace` rather than a guessed encoding: a file the sandbox wrote is UTF-8 and one the user
    # uploaded may be anything, and a few replacement characters beat a 500.
    text = head.decode("utf-8", "replace")
    # The extension is a claim, not a fact. A NUL byte is what actually separates a text file
    # from a parquet somebody named `.csv` — without this, `csv.reader` is happy to report the
    # first 40 bytes of it as a column name.
    if "\x00" in text:
        return {**empty, "kind": "none"}
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
            # Ragged rows are normal in a file a script appended to. Padded rather than dropped,
            # so the table stays rectangular and the gap shows as an empty cell.
            rows = [row[: len(columns)] + [""] * (len(columns) - len(row)) for row in parsed[1:]]

    if columns:
        return {
            "kind": "table",
            "columns": columns,
            "rows": rows,
            "text": "",
            "bytes": size,
            "truncated": clipped or more,
        }
    return {**empty, "kind": "text", "text": text, "truncated": clipped}


@router.delete("/{chat_id}/files/{path:path}")
async def discard(chat_id: str, path: str) -> dict:
    """The composer's ✕ deletes the file and not only the chip: `uploads/` is what the prompt is
    built from every turn, so a chip removed while the file stayed would keep describing it."""
    async with _sandbox() as sandbox:
        await sandbox.delete(chat_id, path)
    return {"deleted": path}
