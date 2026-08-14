"""Files in and out of a chat's workspace.

The workspace is the store: there is no `files` table, because the directory is a bind mount the
sandbox already works in and a row would only ever describe what `ls` can answer. So an upload is
bytes plus a profile, and getting something back is a read through `scratch.resolve`.

Uploading is not a tool call and writes no event of its own. What records it is the *user* event
of the turn it is attached to — `POST /messages` takes the paths — so a replayed transcript shows
the file above the question that used it, and nothing here has to invent a turn to belong to.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from ..sandbox import profile, scratch

router = APIRouter(prefix="/api")

MAX_BYTES = 100 * 1024 * 1024
"""Nothing bounds `DATA_DIR`, so a 4 GB CSV is silent disk consumption in someone's home
directory. 100 MB is more than the sandbox can hold in memory anyway."""
TOO_BIG = f"that file is larger than the {MAX_BYTES // 1024 // 1024} MB limit"
CHUNK = 1024 * 1024

INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
"""Rendered in the transcript rather than downloaded, and raster only: an SVG is a document that
can carry script, and serving one inline from this origin would hand it the portal's cookie. The
type is derived from the extension here — a browser-supplied content type is never echoed back."""


@router.post("/chats/{chat_id}/files")
async def upload(chat_id: str, file: UploadFile, request: Request) -> dict:
    """Take a file into this chat's workspace and read its shape.

    The filename is a browser's, so `scratch.save_upload` is what turns it into a path — one
    place, checked on the resolved target.
    """
    if await request.app.state.store.get(chat_id) is None:
        raise HTTPException(404, "no such chat")

    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, TOO_BIG)

    # Content-Length is the cheap refusal, not a promise — a chunked upload sends none at all.
    body = bytearray()
    while chunk := await file.read(CHUNK):
        body += chunk
        if len(body) > MAX_BYTES:
            raise HTTPException(413, TOO_BIG)

    try:
        path = scratch.save_upload(chat_id, file.filename or "upload", bytes(body))
    except ValueError as exc:
        # Only reachable if `uploads/` is itself a symlink out of the workspace, which a script
        # in the sandbox could plant. A sentence rather than a stack trace.
        raise HTTPException(400, str(exc)) from None
    read = await profile.of(chat_id, path)
    return {
        "path": path,
        "bytes": read.bytes,
        "rows": read.rows,
        "columns": len(read.columns),
        "note": read.note,
    }


# Below the chat routes that end in a fixed segment, and `{path:path}` because an artifact is
# `charts/q1.png` as readily as `q1.png`.
@router.get("/chats/{chat_id}/files/{path:path}")
async def download(chat_id: str, path: str) -> FileResponse:
    target, refused = scratch.resolve(chat_id, path)
    if target is None:
        raise HTTPException(400, refused)
    if not target.is_file():
        raise HTTPException(404, f"no file at {path!r} in this chat's workspace")

    inline = Path(path).suffix.lower() in INLINE
    return FileResponse(
        target,
        media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        filename=None if inline else Path(path).name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/chats/{chat_id}/files/{path:path}")
async def discard(chat_id: str, path: str) -> dict:
    """Take back a file that was sent by mistake.

    Reachable only from the composer, whose chips are cleared the moment a question is sent — so
    what this can remove is always a file no turn has used yet. Without it the ✕ on a chip would
    be a lie: the prompt is built from the directory, so a file still on disk is one the agent is
    still told it has.
    """
    target, refused = scratch.resolve(chat_id, path)
    if target is None:
        raise HTTPException(400, refused)
    if not target.is_file():
        raise HTTPException(404, f"no file at {path!r} in this chat's workspace")
    target.unlink()
    profile.forget(chat_id, path)
    return {"deleted": path}
