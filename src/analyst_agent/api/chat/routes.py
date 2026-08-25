"""The ten routes of a chat in use: one turn, the files beside it, the rows it produced.

Making, naming and deleting a chat is `chat_management/`. Both routers share `/api/chats`, and
nothing there goes deeper than `/{chat_id}`, so no route can shadow one here whichever loads
first — `test_every_route_is_reachable_and_unshadowed` is what says so.

The two greedy `{path:path}` parameters sit under a literal `files/` or `preview/` segment, which
is what keeps them off `/messages` and `/stop`. That is a property of those literals rather than
of the order.

**No `SandboxError` handling here.** `server.py` maps it once at the app boundary, because the
service supplies its own status and the file routes would otherwise repeat the same three lines.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from ...agent.turn import stream_turn
from ...core import transcript
from ..support.stream import turn_stream
from . import controller
from .controller import CHUNK, MAX_BYTES, TOO_BIG
from .dto import Decided, Decision, Discarded, FilePreview, Question, ResultPage, Stopped, Uploaded

router = APIRouter(prefix="/api/chats")

# A result is addressed by its own id and not through the chat that produced it: the
# browser has the id from a `tool_end` event, and a table in an old transcript is a link.
results = APIRouter(prefix="/api/results")

# Raster only, and the type is derived from the extension rather than echoing the browser's: an
# SVG is a document that can carry script, and serving one inline would hand it this origin.
INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@router.get("/{chat_id}/events")
async def read_events(chat_id: str) -> list[dict]:
    """The chat's whole event log, in order — this is the transcript the browser replays.
    No response model: the events are a union whose one definition is `core/events.py`, and
    naming them again here would be a third copy of the wire format."""
    return await controller.events(chat_id)


@router.post("/{chat_id}/stop", response_model=Stopped)
async def stop_turn(chat_id: str, request: Request) -> Stopped:
    """Ask the turn in flight to stop, and say whether there was one.
    Never a 404: a stop that lands after the turn ended has done its job, and an error there
    would put a red banner on a finished answer."""
    return controller.stop(request.app, chat_id)


@router.post("/{chat_id}/approve", response_model=Decided)
async def approve_script(chat_id: str, body: Decision, request: Request) -> Decided:
    """Answer the script this chat is waiting on: yes, no, or always for the rest of the session.
    404 when nothing is waiting — a click that arrives after the turn ended must not read as
    having approved anything."""
    decided = controller.approve(request.app, chat_id, body.decision)
    if decided is None:
        raise HTTPException(404, "nothing in this chat is waiting to be approved")
    return decided


@router.post("/{chat_id}/messages")
async def send_message(chat_id: str, body: Question, request: Request) -> StreamingResponse:
    """One turn of the question agent, streamed as server-sent events.
    404 before the stream opens if there is no such chat: once the generator is handed over there
    is no way left to report anything but an event, and a refused turn would read as a silent
    one."""
    ready = await controller.prepare(request.app, chat_id, body.question, body.files)
    if ready is None:
        raise HTTPException(404, "no such chat")
    cube, attached, approvals = ready

    return turn_stream(
        request,
        chat_id,
        lambda cancel: stream_turn(
            chat_id, body.question, request.app.state.saver, cube, cancel, attached, approvals
        ),
    )


# --- the files beside a chat -------------------------------------------------


@router.post("/{chat_id}/files", response_model=Uploaded)
async def upload_file(chat_id: str, file: UploadFile, request: Request) -> Uploaded:
    """Hand a file to the sandbox and report the shape it read out of it.
    413 past the size limit, checked twice — Content-Length is the cheap refusal and not a
    promise, since a chunked upload sends none at all."""
    if await transcript.get(chat_id) is None:
        raise HTTPException(404, "no such chat")

    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, TOO_BIG)

    body = bytearray()
    while chunk := await file.read(CHUNK):
        body += chunk
        if len(body) > MAX_BYTES:
            raise HTTPException(413, TOO_BIG)

    return await controller.store(chat_id, file.filename or "upload", bytes(body))


# Below the chat routes that end in a fixed segment, and `{path:path}` because an artifact is
# `charts/q1.png` as readily as `q1.png`.
@router.get("/{chat_id}/files/{path:path}")
async def download_file(chat_id: str, path: str) -> Response:
    """One workspace file's bytes, inline for an image and as an attachment for anything else.
    The content type is derived from the extension and never echoed from the upload: an SVG can
    carry script, and serving one inline would hand it this origin."""
    data = await controller.read_bytes(chat_id, path)
    inline = Path(path).suffix.lower() in INLINE
    headers = {"X-Content-Type-Options": "nosniff"}
    if not inline:
        headers["Content-Disposition"] = f'attachment; filename="{Path(path).name}"'
    return Response(
        data,
        media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        headers=headers,
    )


@router.get("/{chat_id}/preview/{path:path}", response_model=FilePreview)
async def preview_file(chat_id: str, path: str) -> FilePreview:
    """The head of a workspace file, as a table if it parses as one and as text otherwise.
    Never reads the file at all for bytes with no useful head — the extension decides that before
    a request reaches the service."""
    return await controller.preview(chat_id, path)


@router.delete("/{chat_id}/files/{path:path}", response_model=Discarded)
async def discard_file(chat_id: str, path: str) -> Discarded:
    """Take a file back off the workspace, deleting it rather than only un-attaching it.
    `uploads/` is what the prompt is built from every turn, so a chip dropped while the file
    stayed would leave the agent still describing it."""
    return await controller.discard(chat_id, path)


# --- the rows a turn produced, on their own prefix ---------------------------


@results.get("/{result_id}", response_model=ResultPage)
async def read_result(result_id: str, offset: int = 0, limit: int = 200) -> ResultPage:
    """One page of a stored result set, plus the row count of the whole set.
    404 when the id is unknown, which a link in an old transcript will hit: rows are pruned on a
    retention pass and outlive nothing."""
    found = await controller.page(result_id, offset, limit)
    if found is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    return found


@results.get("/{result_id}/csv")
async def export_csv(result_id: str) -> Response:
    """The whole result set as a CSV attachment, with column labels as its header.
    No `response_model`: the body is text/csv, and the browser reaches this as a plain link
    authenticated by the token cookie rather than through `fetch`."""
    exported = await controller.as_csv(result_id)
    if exported is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    return Response(
        exported,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="result-{result_id[:8]}.csv"'},
    )
