"""Editing the model on a branch.

Tiers 1-2 run on save, and that is the load-bearing moment: in dev mode Cube watches the model
mount, so a file reaches the *live* source the instant it is written and one that will not
compile takes the whole thing down. Tier 3 is its own button because it reports rather than
loads. Saving to main is refused — main is what the pipeline generates.
"""

import asyncio
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..chat.service import stream_edit_turn
from ..cube.client import CubeError, catalog_of
from ..grounding import for_source
from ..sources import docker
from ..sources.edit import impact, workspace
from ..sources.model import publish, vcs
from ..sources.registry import Source
from .state import find
from .stream import sse

router = APIRouter(prefix="/api")


class NewBranch(BaseModel):
    name: str


class Message(BaseModel):
    question: str


class FileBody(BaseModel):
    text: str


class CommitBody(BaseModel):
    message: str = ""


# --- shared -------------------------------------------------------------------


def _idle(request: Request) -> None:
    """One job at a time, machine-wide — a checkout restarts the container and so does a step."""
    if request.app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")


async def _repo(source: Source) -> Path:
    """The model directory as a repo. `ensure` is idempotent, which is the whole backfill."""
    path = source.model_dir()
    result = await vcs.ensure(path)
    if not result.ok:
        raise HTTPException(400, f"could not version {path}: {result.message}")
    return path


STATUS = {"branch": 409, "repo": 400, "path": 400, "missing": 404, "rejected": 400}
"""Which status a refusal is. The kinds are the workspace's; the codes are HTTP's."""


async def _on_a_branch(source: Source) -> str:
    """The branch to edit on, or the refusal as a status. One rule, in `workspace`, so the
    routes and the editor's tools cannot come to disagree about what is editable."""
    if refusal := await workspace.editable(source):
        raise HTTPException(STATUS[refusal.kind], refusal.reason)
    return await vcs.current(source.model_dir())


def _dirty(dirty: list[str]) -> None:
    if dirty:
        raise HTTPException(409, f"uncommitted changes to {', '.join(dirty)} — commit or discard")


async def _restart(request: Request, source: Source) -> None:
    """Load what is now checked out — deterministically, rather than trusting the file watcher
    to have seen a whole branch change."""
    result = await docker.restart(source.service)
    request.app.state.health.pop(source.name, None)
    if not result.ok:
        raise HTTPException(400, result.message or f"could not restart {source.service}")


async def _impact(request: Request, source: Source) -> impact.Impact:
    """What the checked-out model would break. Read from this source's own `/v1/meta`, not the
    agent's cached catalog — that one follows CUBE_URL and this source may not be it."""
    try:
        catalog = await catalog_of(source)
    except CubeError as exc:
        raise HTTPException(409, f"cannot read the served model to check impact: {exc}") from None
    return await impact.check(
        source.name, catalog, request.app.state.dashboards, for_source(source)
    )


def _validation(validation: publish.Validation) -> dict:
    return {
        "compiled": validation.compiled,
        "compile_error": validation.compile_error,
        "entities": validation.entities,
        "member_errors": validation.member_errors,
    }


# --- branches -----------------------------------------------------------------


@router.get("/sources/{name}/branches")
async def list_branches(name: str, request: Request) -> dict:
    source = await find(request, name)
    path = await _repo(source)
    current = await vcs.current(path)
    out = []
    for branch in await vcs.branches(path):
        # Files changed, not commits: it is what a reviewer is about to read.
        changed = [] if branch == vcs.MAIN else await vcs.changed(path, vcs.MAIN, branch)
        out.append({"name": branch, "current": branch == current, "changed": len(changed)})
    return {
        "current": current,
        "main": vcs.MAIN,
        "dirty": await vcs.status(path),
        "branches": out,
    }


@router.post("/sources/{name}/branches")
async def create_branch(name: str, body: NewBranch, request: Request) -> dict:
    source = await find(request, name)
    _idle(request)
    path = await _repo(source)
    if error := vcs.validate_branch(body.name, await vcs.branches(path)):
        raise HTTPException(400, error)
    _dirty(await vcs.status(path))

    result = await vcs.create(path, body.name)
    if not result.ok:
        raise HTTPException(400, result.message)
    # No restart: a fresh branch is the commit already loaded.
    return await list_branches(name, request)


@router.post("/sources/{name}/branches/{branch}/checkout")
async def checkout(name: str, branch: str, request: Request) -> dict:
    source = await find(request, name)
    _idle(request)
    path = await _repo(source)
    _dirty(await vcs.status(path))

    result = await vcs.checkout(path, branch)
    if not result.ok:
        raise HTTPException(400, result.message)
    await _restart(request, source)
    return await list_branches(name, request)


@router.delete("/sources/{name}/branches/{branch}")
async def delete_branch(name: str, branch: str, request: Request, force: bool = False) -> dict:
    source = await find(request, name)
    _idle(request)
    path = await _repo(source)
    if branch == await vcs.current(path):
        raise HTTPException(409, f"{branch!r} is checked out — switch away from it first")
    result = await vcs.delete(path, branch, force=force)
    if not result.ok:
        raise HTTPException(400, result.message)
    return await list_branches(name, request)


@router.get("/sources/{name}/branches/{branch}/diff")
async def branch_diff(name: str, branch: str, request: Request) -> dict:
    """What merging this branch would apply to main, and what it would break."""
    source = await find(request, name)
    path = await _repo(source)
    # Impact is measured against the *served* model, so it means nothing for a branch that is
    # not the one checked out. Reporting a number from the wrong model would be worse than
    # reporting none.
    report = await _impact(request, source) if branch == await vcs.current(path) else None
    return {
        "branch": branch,
        "changed": [{"status": s, "path": p} for s, p in await vcs.changed(path, vcs.MAIN, branch)],
        "patch": await vcs.diff(path, vcs.MAIN, branch),
        "dirty": await vcs.status(path),
        "impact": report and asdict(report),
    }


@router.post("/sources/{name}/branches/{branch}/merge")
async def merge(name: str, branch: str, request: Request, force: bool = False) -> dict:
    """Install this branch and make it main. No deploy — main is served once checked out."""
    source = await find(request, name)
    _idle(request)
    path = await _repo(source)
    if branch == vcs.MAIN:
        raise HTTPException(400, "that is main")
    _dirty(await vcs.status(path))

    was = await vcs.current(path)
    if branch != was:
        raise HTTPException(
            409,
            f"check {branch!r} out before merging it — impact is measured against the model "
            "actually being served, and nothing else can measure it",
        )
    # While the branch is still the served model. Afterwards there is nothing to refuse.
    report = await _impact(request, source)
    if not report.ok and not force:
        raise HTTPException(409, report.as_message())

    switched = await vcs.checkout(path, vcs.MAIN)
    if not switched.ok:
        raise HTTPException(400, switched.message)

    merged = await vcs.merge(path, branch)
    if not merged.ok:
        # `vcs.merge` aborted, so going back is safe. Leaving someone on main after a failed
        # merge would silently change what the source serves.
        await vcs.checkout(path, was)
        raise HTTPException(409, f"could not merge {branch}: {merged.message}")

    await _restart(request, source)
    return {
        "merged": branch,
        "current": await vcs.current(path),
        "impact": asdict(report),
        **_validation(await publish.check(source)),
    }


# --- files --------------------------------------------------------------------


@router.get("/sources/{name}/files")
async def list_files(name: str, request: Request) -> dict:
    source = await find(request, name)
    path = await _repo(source)
    branch = await vcs.current(path)
    return {
        "branch": branch,
        "editable": branch != vcs.MAIN,
        "dirty": await vcs.status(path),
        "files": sorted(workspace.files(source)),
    }


@router.get("/sources/{name}/files/{path:path}")
async def read_file(name: str, path: str, request: Request) -> dict:
    source = await find(request, name)
    target, refused = workspace.resolve(source, path)
    if target is None:
        raise HTTPException(400, refused)
    if not target.is_file():
        raise HTTPException(404, f"no model file at {path!r}")
    return {"path": path, "text": target.read_text()}


@router.put("/sources/{name}/files/{path:path}")
async def write_file(name: str, path: str, body: FileBody, request: Request) -> dict:
    """Tiers 1-2, then the write. What the gate refuses never reaches disk."""
    source = await find(request, name)
    outcome = await workspace.write(source, path, body.text)
    if not outcome.ok:
        raise HTTPException(STATUS[outcome.kind], outcome.reason)
    return {
        "path": outcome.path,
        "warnings": outcome.warnings,
        "dirty": await vcs.status(source.model_dir()),
    }


@router.delete("/sources/{name}/files/{path:path}")
async def delete_file(name: str, path: str, request: Request) -> dict:
    source = await find(request, name)
    outcome = await workspace.delete(source, path)
    if not outcome.ok:
        raise HTTPException(STATUS[outcome.kind], outcome.reason)
    return {"deleted": outcome.path, "dirty": await vcs.status(source.model_dir())}


# --- the working tree ----------------------------------------------------------


@router.post("/sources/{name}/validate")
async def validate(name: str, request: Request) -> dict:
    """Tier 3: restart and ask Cube. This is what makes an edit visible as well as checked."""
    source = await find(request, name)
    _idle(request)
    await _restart(request, source)
    validation = await publish.check(source)
    # Reported here as well as at merge, so a broken tile surfaces while the edit is still
    # under the cursor rather than at the moment someone wanted to land it.
    impacted = await _impact(request, source) if validation.compiled else None
    return {**_validation(validation), "impact": impacted and asdict(impacted)}


@router.post("/sources/{name}/commit")
async def commit(name: str, body: CommitBody, request: Request) -> dict:
    source = await find(request, name)
    repo = await _repo(source)
    branch = await _on_a_branch(source)
    result = await vcs.commit(repo, body.message or f"edit {branch}")
    if not result.ok:
        raise HTTPException(400, result.message)
    return {"branch": branch, "dirty": await vcs.status(repo)}


# --- the editor chat -----------------------------------------------------------


@router.get("/sources/{name}/chats")
async def list_edit_chats(name: str, request: Request) -> list[dict]:
    await find(request, name)
    return await request.app.state.store.model_chats(name)


@router.post("/sources/{name}/chats")
async def create_edit_chat(name: str, request: Request) -> dict:
    """A chat bound to this source. Editing needs a branch, so refuse on main."""
    source = await find(request, name)
    branch = await _on_a_branch(source)
    chat = await request.app.state.store.create_model_chat(name, branch)
    return {"id": chat.id, "title": chat.title, "branch": branch}


@router.post("/sources/{name}/chats/{chat_id}/messages")
async def send_edit_message(
    name: str, chat_id: str, body: Message, request: Request
) -> StreamingResponse:
    source = await find(request, name)
    store = request.app.state.store
    if await store.model_chat_source(chat_id) != name:
        raise HTTPException(404, f"no model chat {chat_id!r} on {name!r}")

    cancel = asyncio.Event()
    request.app.state.cancels[chat_id] = cancel

    async def events():
        try:
            async for event in stream_edit_turn(
                chat_id, body.question, source, store, request.app.state.saver, cancel
            ):
                yield event
                # A closed tab should not leave the agent burning model calls.
                if await request.is_disconnected():
                    cancel.set()
        finally:
            request.app.state.cancels.pop(chat_id, None)

    return sse(events())


@router.post("/sources/{name}/discard")
async def discard(name: str, request: Request) -> dict:
    source = await find(request, name)
    _idle(request)
    repo = await _repo(source)
    await _on_a_branch(source)
    result = await vcs.discard(repo)
    if not result.ok:
        raise HTTPException(400, result.message)
    await _restart(request, source)
    return {"dirty": await vcs.status(repo)}
