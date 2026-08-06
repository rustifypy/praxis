"""The portal's HTTP surface.

Localhost, one user, no login — a token in the URL, the way Jupyter does it. The token
proves the request came from the browser we opened rather than another process on the
machine; it is not an identity system.

Identity is resolved here and never read from a client header. `user_id` is signed into
the Cube JWT, so trusting a header would be the hole under any row-level security added
later, however localhost the setup is today.
"""

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent.build import SecurityContext, load_catalog
from ..chat.service import stream_turn
from ..chat.store import ChatStore, checkpointer
from ..config import settings
from ..cube.catalog import Catalog
from ..cube.client import CubeClient, mint_token

STATIC = Path(__file__).parent / "static"
COOKIE = "analyst_token"
# Regenerated every launch, like a Jupyter server token.
TOKEN = secrets.token_urlsafe(24)


def identity() -> SecurityContext:
    """Who the caller is. Server-side, deliberately — see the module docstring."""
    return SecurityContext(user_id="local")


class NewChat(BaseModel):
    title: str = "New chat"


class Rename(BaseModel):
    title: str


class Message(BaseModel):
    question: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ChatStore()
    await store.init()
    async with checkpointer() as saver:
        app.state.store = store
        app.state.saver = saver
        # One cancel flag per in-flight turn, so the stop button has something to set.
        app.state.cancels = {}
        yield


app = FastAPI(title="analyst-agent web", lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path == "/api/healthz":
        return await call_next(request)
    supplied = request.query_params.get("token") or request.cookies.get(COOKIE)
    if not supplied or not secrets.compare_digest(supplied, TOKEN):
        return Response("Unauthorized. Open the URL printed by `analyst web`.", status_code=401)
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
    chats = await request.app.state.store.list_chats(identity().user_id)
    return [c.__dict__ for c in chats]


@app.post("/api/chats")
async def create_chat(body: NewChat, request: Request) -> dict:
    chat = await request.app.state.store.create(body.title, identity().user_id)
    return chat.__dict__


@app.patch("/api/chats/{chat_id}")
async def rename_chat(chat_id: str, body: Rename, request: Request) -> dict:
    await request.app.state.store.rename(chat_id, body.title)
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, request: Request) -> dict:
    await request.app.state.store.delete(chat_id)
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

    async def sse():
        try:
            async for event in stream_turn(
                chat_id, body.question, store, request.app.state.saver, identity(), cancel
            ):
                yield f"data: {event.model_dump_json()}\n\n"
                # A closed tab should not leave the agent burning model calls.
                if await request.is_disconnected():
                    cancel.set()
        finally:
            request.app.state.cancels.pop(chat_id, None)

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        # Without these a proxy or the browser will sit on the stream and the
        # transcript arrives in one lump at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/catalog")
async def catalog(request: Request, refresh: bool = False) -> dict:
    client = CubeClient(mint_token(identity().as_claims()))
    try:
        cat: Catalog = await load_catalog(client, refresh=refresh)
    finally:
        await client.aclose()
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
