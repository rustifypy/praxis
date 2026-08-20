import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import cube, repo

ROOT = Path(os.environ.get("MODEL_STORE_PATH", "/data/model.git"))
READ_TOKEN = os.environ.get("MODEL_STORE_READ_TOKEN", "")
WRITE_TOKEN = os.environ.get("MODEL_STORE_WRITE_TOKEN", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed: an unauthenticated store beside a warehouse is not a default anyone
    # should reach by omission.
    if not READ_TOKEN or not WRITE_TOKEN:
        raise RuntimeError("MODEL_STORE_READ_TOKEN and MODEL_STORE_WRITE_TOKEN are required")
    # Cube is not reachable except through here, so a missing secret is not a degraded query
    # path — it is no query path at all, and better said at startup than per request.
    if not cube.API_SECRET:
        raise RuntimeError("CUBEJS_API_SECRET is required — queries are proxied to Cube")
    await repo.ensure(ROOT)
    yield


app = FastAPI(title="model-store", lifespan=lifespan)


def _supplied(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else ""


def reader(request: Request) -> None:
    token = _supplied(request)
    if any(secrets.compare_digest(token, t) for t in (READ_TOKEN, WRITE_TOKEN) if t):
        return
    raise HTTPException(401, "a read or write token is required")


def writer(request: Request) -> None:
    if WRITE_TOKEN and secrets.compare_digest(_supplied(request), WRITE_TOKEN):
        return
    raise HTTPException(401, "a write token is required")


@app.exception_handler(repo.RepoError)
async def _repo_error(request: Request, exc: repo.RepoError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


class Files(BaseModel):
    files: dict[str, str]
    message: str = "update model"


class Edit(BaseModel):
    write: dict[str, str] = Field(default_factory=dict)
    delete: list[str] = Field(default_factory=list)
    message: str = "edit model"


class NewRef(BaseModel):
    name: str
    from_ref: str = repo.MAIN


class Merge(BaseModel):
    from_ref: str
    into: str = repo.MAIN
    message: str = ""


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "refs": len(await repo.refs(ROOT))}


@app.get("/refs", dependencies=[Depends(reader)])
async def list_refs() -> dict:
    return {"refs": await repo.refs(ROOT)}


@app.post("/refs", dependencies=[Depends(writer)])
async def new_ref(body: NewRef) -> dict:
    return {"name": body.name, "sha": await repo.create_ref(ROOT, body.name, body.from_ref)}


@app.delete("/refs/{name:path}", dependencies=[Depends(writer)])
async def drop_ref(name: str) -> dict:
    await repo.delete_ref(ROOT, name)
    return {"deleted": name}


@app.get("/version", dependencies=[Depends(reader)])
async def version(ref: str = repo.MAIN) -> dict:
    return {"ref": ref, "sha": await repo.version(ROOT, ref)}


@app.get("/files", dependencies=[Depends(reader)])
async def all_files(ref: str = repo.MAIN) -> dict:
    found = await repo.files(ROOT, ref)
    return {"ref": ref, "sha": await repo.version(ROOT, ref), "files": found}


@app.get("/files/{path:path}", dependencies=[Depends(reader)], response_class=PlainTextResponse)
async def one_file(path: str, ref: str = repo.MAIN) -> str:
    return await repo.one_file(ROOT, ref, path)


@app.put("/files", dependencies=[Depends(writer)])
async def replace(body: Files, ref: str = repo.MAIN) -> dict:
    sha = await repo.commit_files(ROOT, ref, body.files, [], body.message, replace=True)
    return {"ref": ref, "sha": sha}


@app.patch("/files", dependencies=[Depends(writer)])
async def amend(body: Edit, ref: str = repo.MAIN) -> dict:
    sha = await repo.commit_files(ROOT, ref, body.write, body.delete, body.message, replace=False)
    return {"ref": ref, "sha": sha}


@app.get("/diff", dependencies=[Depends(reader)])
async def diff(ref: str, base: str = repo.MAIN) -> dict:
    return await repo.diff(ROOT, base, ref)


@app.post("/merge", dependencies=[Depends(writer)])
async def merge(body: Merge) -> dict:
    message = body.message or f"merge {body.from_ref} into {body.into}"
    return {"into": body.into, "sha": await repo.merge(ROOT, body.from_ref, body.into, message)}


@app.get("/log", dependencies=[Depends(reader)])
async def log(ref: str = repo.MAIN, limit: int = 50) -> dict:
    return {"commits": await repo.log(ROOT, ref, limit)}


# --- Cube ---------------------------------------------------------------------
# Below every fixed route, and `{path:path}` because Cube's own paths have several segments.


@app.api_route("/cube/{path:path}", methods=["GET", "POST"], dependencies=[Depends(writer)])
async def query(path: str, request: Request) -> Response:
    """Cube, for the ref named in `X-Model-Ref`.

    **The write token only**, deliberately: the read token is the one handed to Cube itself, and
    letting it query back through this proxy would be circular — the credential given away would
    be as capable as the one kept. Read-level query scoping is worth its own token later; for one
    portal it is the write token or nothing.
    """
    ref = request.headers.get(cube.REF_HEADER) or repo.MAIN
    try:
        answer = await cube.forward(
            request.method,
            path,
            ref,
            body=await request.body(),
            query=str(request.url.query or ""),
        )
    except ValueError as exc:
        # A secret too short to sign with. Configuration, so 500 rather than a Cube error.
        raise HTTPException(500, str(exc)) from None
    except httpx.RequestError as exc:
        # Cube is down or still booting. 502, so the caller can tell "Cube is unreachable" from
        # "Cube refused this query" — the second is a 400 with a body it can act on.
        raise HTTPException(502, f"cube did not answer: {exc}") from None

    # Cube's body and status verbatim. Its content-type too: `/readyz` is text and the query
    # API is JSON, and this service should not be the thing that decides which.
    return Response(
        answer.content,
        status_code=answer.status_code,
        media_type=answer.headers.get("content-type"),
    )
