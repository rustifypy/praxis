"""The sandbox over HTTP. One token, same shape as the model store's.

Every path a caller supplies goes through `workspace.resolve`, which checks the *resolved*
target — so one check covers `..`, an absolute path, and a symlink a script planted itself.
"""

import json
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import runner, workspace

TOKEN = os.environ.get("SANDBOX_TOKEN", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed. This runs arbitrary Python; an unauthenticated one is not a default
    # anyone should reach by omission.
    if not TOKEN:
        raise RuntimeError("SANDBOX_TOKEN is required")
    workspace.ROOT.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="analyst-sandbox", lifespan=lifespan)


def authorised(request: Request) -> None:
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not secrets.compare_digest(supplied, TOKEN):
        raise HTTPException(401, "a token is required")


class RunBody(BaseModel):
    chat_id: str
    script: str
    timeout: float = runner.EXEC_TIMEOUT_S


class PathBody(BaseModel):
    chat_id: str
    path: str


def _target(chat_id: str, path: str):
    resolved, refused = workspace.resolve(chat_id, path)
    if resolved is None:
        raise HTTPException(400, refused)
    return resolved


CONTRACT = 1
"""The HTTP contract this service implements, reported on `/health` like the model store's.

A compatibility integer, not a version: bumped only when a route, a body or a header changes in
a way a portal built against the previous number would get wrong.
"""


@app.get("/health")
async def health() -> dict:
    """Unauthenticated, so it can be checked before anyone knows the tokens match. It says
    nothing a caller could not learn by connecting to the port."""
    return {
        "ok": True,
        "contract": CONTRACT,
        "root": str(workspace.ROOT),
        "running": sum(1 for _ in _busy()),
    }


def _busy():
    return (c for c in runner._running if runner.busy(c))


@app.post("/run", dependencies=[Depends(authorised)])
async def run(body: RunBody) -> StreamingResponse:
    """Stream a script's output, then one result.

    JSON lines rather than SSE: the caller is a Python client rather than a browser, and the
    portal re-frames this into its own event stream anyway.
    """
    if runner.busy(body.chat_id):
        raise HTTPException(409, "a script is already running in this chat")

    async def lines() -> AsyncIterator[bytes]:
        async for event in runner.run(body.chat_id, body.script, body.timeout):
            yield json.dumps(event).encode() + b"\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/kill", dependencies=[Depends(authorised)])
async def kill(body: PathBody) -> dict:
    return {"killed": await runner.kill(body.chat_id)}


async def _profile(chat_id: str, path: str, size: int) -> dict:
    """One file's shape, kept beside it. Never raises: an upload whose profile failed is still
    an upload, and the reason belongs in `note` where both the agent and the person who sent it
    can read it."""
    try:
        profile = json.loads(await runner.profile(chat_id, path))
    except (RuntimeError, ValueError) as exc:
        profile = {"note": f"not profiled — {exc}"}
    profile = {**profile, "path": path, "bytes": size}
    runner.keep(chat_id, path, profile)
    return profile


@app.post("/profile", dependencies=[Depends(authorised)])
async def profile(body: PathBody) -> dict:
    """Re-read one file's shape. Uploading already does this, so this is for a file a script
    wrote that someone wants summarised."""
    target = _target(body.chat_id, body.path)
    if not target.is_file():
        raise HTTPException(404, f"no file at {body.path!r}")
    return {"profile": await _profile(body.chat_id, body.path, target.stat().st_size)}


@app.get("/profiles/{chat_id}", dependencies=[Depends(authorised)])
async def profiles(chat_id: str) -> dict:
    """Every upload's shape, from the cache. Read once per turn to build the prompt, so it is
    one small request rather than one profiling run per file."""
    return {"profiles": runner.cached(chat_id)}


@app.get("/files/{chat_id}", dependencies=[Depends(authorised)])
async def listing(chat_id: str) -> dict:
    return {"files": workspace.listing(chat_id, "**/*"), "uploads": workspace.uploaded(chat_id)}


@app.get("/files/{chat_id}/{path:path}", dependencies=[Depends(authorised)])
async def read_file(chat_id: str, path: str) -> FileResponse:
    target = _target(chat_id, path)
    if not target.is_file():
        raise HTTPException(404, f"no file at {path!r}")
    return FileResponse(target, filename=target.name)


@app.post("/uploads/{chat_id}", dependencies=[Depends(authorised)])
async def upload(chat_id: str, file: UploadFile) -> dict:
    """A file the user handed over. Named into `uploads/`, which is what makes it
    distinguishable from something a script wrote — no state has to say which is which."""
    data = await file.read()
    try:
        path = workspace.save_upload(chat_id, file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    # Profiled on arrival, which is what stops the first move after every upload being a script
    # to discover the columns — and a 200 MB CSV cannot go into a prompt whole anyway.
    return {"path": path, "profile": await _profile(chat_id, path, len(data))}


@app.delete("/files/{chat_id}/{path:path}", dependencies=[Depends(authorised)])
async def delete_file(chat_id: str, path: str) -> dict:
    target = _target(chat_id, path)
    target.unlink(missing_ok=True)
    runner.forget(chat_id, path)
    return {"deleted": path}


@app.delete("/workspace/{chat_id}", dependencies=[Depends(authorised)])
async def drop(chat_id: str) -> dict:
    """Deleting a chat takes its workspace with it — the directory *is* the record."""
    workspace.remove(chat_id)
    return {"removed": chat_id}
