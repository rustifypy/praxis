"""The portal's HTTP surface."""

import asyncio
import csv
import io
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent.turn import stream_turn
from ..core.config import settings
from ..cube.catalog import Catalog
from ..cube.client import CubeClient
from ..cube.shape import Column, export_value
from ..sandbox import container, scratch
from ..transcript import ChatStore, checkpointer
from . import dashboards, files, jobs, model_edit, sources, state, system
from .stream import sse

STATIC = Path(__file__).parent / "static"
COOKIE = "analyst_token"
# Regenerated every launch, like a Jupyter server token.
TOKEN = secrets.token_urlsafe(24)


class NewChat(BaseModel):
    title: str = "New chat"


class Rename(BaseModel):
    title: str


class Decision(BaseModel):
    decision: Literal["yes", "always", "no"] = "yes"


class Message(BaseModel):
    question: str
    files: list[str] = []
    """Workspace paths the composer attached, from `POST /files`. The user event carries them,
    which is how an upload reaches the transcript at all."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ChatStore()
    await store.init()
    # All three stores open the same file; each runs the one SCHEMA, whoever is first.
    await dashboards.init_state(app)
    await state.init_state(app)
    # One Cube session for the life of the process; its catalog cache is what every query
    # turn reads.
    async with checkpointer() as saver, CubeClient.local() as cube:
        app.state.sandbox = asyncio.create_task(container.rebuild())
        app.state.store = store
        app.state.saver = saver
        app.state.cube = cube
        # One cancel flag per in-flight turn, so the stop button has something to set.
        app.state.cancels = {}
        # And one approval queue, for the chats still being asked. In memory on purpose: a
        # restart is a fair moment to start asking again.
        app.state.approvals = {}
        app.state.always = set()
        yield


app = FastAPI(title="analyst-agent web", lifespan=lifespan)
app.include_router(system.router)
app.include_router(sources.router)
app.include_router(jobs.router)
app.include_router(dashboards.router)
app.include_router(model_edit.router)
app.include_router(files.router)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path == "/api/healthz":
        return await call_next(request)
    supplied = request.query_params.get("token") or request.cookies.get(COOKIE)
    if not supplied or not secrets.compare_digest(supplied, TOKEN):
        return Response("Unauthorized. Open the URL printed by `analyst`.", status_code=401)
    response = await call_next(request)
    if request.query_params.get("token"):
        # Move it out of the query string so reloads and SSE carry it implicitly.
        response.set_cookie(COOKIE, TOKEN, httponly=True, samesite="strict")
    return response


@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/chats")
async def list_chats(request: Request) -> list[dict]:
    chats = await request.app.state.store.list_chats()
    return [c.__dict__ for c in chats]


@app.post("/api/chats")
async def create_chat(body: NewChat, request: Request) -> dict:
    chat = await request.app.state.store.create(body.title)
    return chat.__dict__


@app.patch("/api/chats/{chat_id}")
async def rename_chat(chat_id: str, body: Rename, request: Request) -> dict:
    await request.app.state.store.rename(chat_id, body.title)
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, request: Request) -> dict:
    await request.app.state.store.delete(chat_id)
    scratch.remove(chat_id)
    return {"ok": True}


@app.get("/api/chats/{chat_id}/events")
async def chat_events(chat_id: str, request: Request) -> list[dict]:
    """The transcript. Replaying these is how a reopened chat renders."""
    return await request.app.state.store.events(chat_id)


@app.post("/api/chats/{chat_id}/stop")
async def stop(chat_id: str, request: Request) -> dict:
    cancel = request.app.state.cancels.get(chat_id)
    if cancel:
        cancel.set()
    return {"stopped": bool(cancel)}


@app.post("/api/chats/{chat_id}/approve")
async def approve(chat_id: str, body: Decision, request: Request) -> dict:
    """Answer the script this chat is waiting on. "always" also stops asking for the rest of the
    turn — the tool reads that off the same answer, since it holds the queue and not this dict."""
    queue = request.app.state.approvals.get(chat_id)
    if queue is None:
        raise HTTPException(404, "nothing in this chat is waiting to be approved")
    if body.decision == "always":
        request.app.state.always.add(chat_id)
    queue.put_nowait(body.decision)
    return {"decision": body.decision}


@app.post("/api/chats/{chat_id}/messages")
async def send(chat_id: str, body: Message, request: Request) -> StreamingResponse:
    store = request.app.state.store
    chat = await store.get(chat_id)
    if chat is None:
        raise HTTPException(404, "no such chat")

    # The first question names the chat. Cheap, deterministic, no extra model call.
    if chat.title == "New chat":
        await store.rename(chat_id, body.question[:60])

    cancel = asyncio.Event()
    request.app.state.cancels[chat_id] = cancel
    # Only files actually in the workspace, so the transcript cannot name one that is not there.
    attached = [p for p in body.files if p in scratch.listing(chat_id, "**/*")]

    approvals = None
    if settings.approve_scripts and chat_id not in request.app.state.always:
        approvals = asyncio.Queue()
        request.app.state.approvals[chat_id] = approvals

    async def events():
        try:
            async for event in stream_turn(
                chat_id,
                body.question,
                store,
                request.app.state.saver,
                request.app.state.cube,
                cancel,
                attached,
                approvals,
            ):
                yield event
                # A closed tab should not leave the agent burning model calls — nor a script
                # waiting forever on an approval nobody is there to give.
                if await request.is_disconnected():
                    cancel.set()
        finally:
            request.app.state.cancels.pop(chat_id, None)
            request.app.state.approvals.pop(chat_id, None)

    return sse(events())


@app.get("/api/results/{result_id}")
async def result(result_id: str, request: Request, offset: int = 0, limit: int = 200) -> dict:
    """One page of a stored result set."""
    record = await request.app.state.store.result(result_id)
    if record is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    rows = record["rows"]
    return {
        "id": record["id"],
        "columns": record["columns"],
        "rows": rows[offset : offset + limit],
        "row_count": record["row_count"],
        "offset": offset,
    }


# A segment, not `{result_id}.csv`: a bare path parameter matches "abc.csv" as the id
# itself and shadows the route above it — the same way /sources/{name}/{action} swallowed
# its siblings.
@app.get("/api/results/{result_id}/csv")
async def result_csv(result_id: str, request: Request) -> Response:
    record = await request.app.state.store.result(result_id)
    if record is None:
        raise HTTPException(404, f"no result with id {result_id!r}")
    buffer = io.StringIO()
    columns = [Column(**c) for c in record["columns"]]
    writer = csv.writer(buffer)
    writer.writerow([c.label for c in columns])
    for row in record["rows"]:
        writer.writerow([export_value(row.get(c.id), c) for c in columns])
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="result-{result_id[:8]}.csv"'},
    )


@app.get("/api/catalog")
async def catalog(request: Request, refresh: bool = False) -> dict:
    cat: Catalog = await request.app.state.cube.catalog(refresh=refresh)
    return {
        "cube_url": settings.cube_url,
        "entities": [
            {
                "name": e.name,
                "view": e.is_view,
                "members": len(e.members),
                "description": e.description,
            }
            for e in cat.entities
        ],
    }


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/")
async def index() -> Response:
    page = STATIC / "index.html"
    if not page.is_file():
        return Response(
            "The frontend has not been built. Run `make web`.",
            status_code=503,
            media_type="text/plain",
        )
    return FileResponse(page)
