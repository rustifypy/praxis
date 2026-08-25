"""The app itself: what it holds, who may reach it, and where the routes are mounted.

No route of its own but `/api/healthz` and the page — every surface is a router module, so this
file says what the portal *is* rather than what it does.
"""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core import access
from ..core.config import settings
from ..core.transcript import checkpointer
from ..sandbox import SandboxError
from . import chat, chat_management, dashboard, mcp, model, onboarding
from .support import state

STATIC = Path(__file__).parent / "static"
COOKIE = "analyst_token"

# Minted at import and announced only by the launcher, which is why `uvicorn
# analyst_agent.api.server:app` serves a portal nobody can authenticate against.
TOKEN = secrets.token_urlsafe(24)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.init_state(app)
    # Nothing to warm: every service is one someone else runs, so there is no image to build at
    # startup and no container to bring up.
    async with checkpointer() as saver:
        app.state.saver = saver
        # One cancel flag per in-flight turn, so the stop button has something to set.
        app.state.cancels = {}
        # And one approval queue, for the chats still being asked. In memory on purpose: a
        # restart is a fair moment to start asking again.
        app.state.approvals = {}
        app.state.always = set()
        app.state.mcp_max_rows = settings.max_rows
        # Read once and written through by the routes, so the guard tests memory rather than
        # opening the database on every tool call. After `init_state`, which creates the table.
        app.state.mcp = await access.load()
        # The session manager is started by the mounted app's own lifespan, which a mount does
        # not run — Starlette only runs the outermost. So it is entered here, inside ours, and
        # the tools read `app.state` for a catalog that is already open.
        async with mcp_app.router.lifespan_context(mcp_app):
            yield


app = FastAPI(title="analyst-agent web", lifespan=lifespan)

# The products.
app.include_router(chat_management.router)
app.include_router(chat.router)
app.include_router(chat.results)
app.include_router(dashboard.router)
# What they run on: the connection, the deployment serving it, the pipeline that builds the
# model, and the model itself.
app.include_router(onboarding.router)
app.include_router(onboarding.pipeline)
app.include_router(model.router)
# What something else gets to call.
app.include_router(mcp.router)

mcp_server = mcp.build(app)
app.state.mcp_server = mcp_server
mcp_app = mcp_server.streamable_http_app(streamable_http_path="/")
app.mount("/mcp", mcp_app)


@app.exception_handler(SandboxError)
async def sandbox_failed(request: Request, exc: SandboxError) -> Response:
    """A sandbox failure as a status, in one place instead of in every route that touches one.

    The service's own status when it gave one; 503 when it did not, which covers both "no sandbox
    is configured" and "it did not answer" — a working deployment that cannot run Python either
    way, never a broken portal.

    Here rather than in the workspace controller because it is the same three lines for four routes,
    and because a controller that raises `HTTPException` is one that cannot be called from anything
    but a request. The chat routes do *not* rely on this: they suppress `SandboxError` deliberately,
    so deleting a chat still succeeds when the sandbox is down.
    """
    return JSONResponse({"detail": str(exc)}, status_code=exc.status or 503)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path == "/api/healthz":
        return await call_next(request)
    if request.url.path.startswith("/mcp"):
        # Not `access`, which is now the module this reads its type from — a local of that name
        # would shadow it, and the next person to call `access.load()` in here would get a row.
        granted = request.app.state.mcp
        if not granted.enabled:
            return Response("Not Found", status_code=404)
        header = request.headers.get("authorization", "")
        offered = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if not offered or not secrets.compare_digest(offered, granted.token):
            return Response("Unauthorized.", status_code=401)
        if request.url.path == "/mcp":
            request.scope["path"] = "/mcp/"
        return await call_next(request)

    supplied = request.query_params.get("token") or request.cookies.get(COOKIE)
    if not supplied or not secrets.compare_digest(supplied, TOKEN):
        return Response("Unauthorized. Open the URL printed by `analyst`.", status_code=401)
    response = await call_next(request)
    if request.query_params.get("token"):
        response.set_cookie(COOKIE, TOKEN, httponly=True, samesite="strict")
    return response


@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True}


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
