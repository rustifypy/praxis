"""The four workspace routes.

Registered after `web/chat/`'s, which share the `/api/chats` prefix: the two greedy
`{path:path}` parameters here sit under a literal `files/` or `preview/` segment, so they cannot
reach `/messages` or `/stop` whichever router loads first — but that is a property of those
literals and not of the order, and `test_every_route_is_reachable_and_unshadowed` is what says so.

**No `SandboxError` handling here.** `server.py` maps it once at the app boundary, because the
service supplies its own status and all four of these would otherwise repeat the same three lines.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ...core import transcript
from . import controller
from .controller import CHUNK, MAX_BYTES, TOO_BIG
from .dto import Discarded, FilePreview, Uploaded

router = APIRouter(prefix="/api/chats")

# Raster only, and the type is derived from the extension rather than echoing the browser's: an
# SVG is a document that can carry script, and serving one inline would hand it this origin.
INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


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
