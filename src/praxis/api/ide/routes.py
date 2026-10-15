"""The sixteen editor routes.

**Answers 200 for a model the gate or Cube refuses.** A rejection is the answer to the question,
not a failed request — the editor renders the reasons in one banner, and splitting them across a
400 and a 200 would make one button read two code paths. Only a bad *request* is a status code:
main is not editable, a path is not a model file, a delete names nothing, or the store is
unreachable.
"""

from contextlib import suppress
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...agent.turn import stream_edit_turn
from ...core import boards, transcript
from ...core.deployment import MAIN
from ...modelstore import StoreError, definitions, model_files, served_catalog
from ...modelstore.client import open_store
from ...modelstore.editing import drafts, workspace
from ...modelstore.modelgen import publish
from ..support import state
from ..support.stream import turn_stream
from . import controller
from .controller import STATUS
from .dto import Draft, FileBody, Message, NewRef, Question

router = APIRouter(prefix="/api/ide")


@router.get("/model")
async def served_model(ref: str = MAIN) -> dict:
    """The compiled model for one ref — every entity, every member, and the file each came from.

    Takes a ref because one Cube compiles every ref independently, so this is answerable for the
    branch being edited and not only for main. It is how someone checks that the view they just
    saved exposes the members they meant: without it, the only way to see a branch's compiled
    model would be to merge it into main first.

    Which file defines each entity is read from the store rather than assumed from
    `cubes/<name>.yml`, because a hand-edited model may put a cube anywhere.
    """
    try:
        stored = await model_files(ref)
    except StoreError:
        # One of the two halves being unreachable must not hide the other: without the store
        # nothing names the file an entity came from, and Cube can still say what it serves.
        stored = {}
    defined, _ = definitions.read(stored)

    try:
        catalog = await served_catalog(ref=ref)
    except StoreError as exc:
        # Cube being unreachable does not stop the editing: the files are still readable.
        return {
            "serving": False,
            "error": str(exc)[:400],
            "entities": [],
            "unloaded": sorted({entity.file for entity in defined.values()}),
        }

    return {
        "serving": True,
        "error": "",
        # Shipped rather than recomputed in the browser: counting over cubes only is a fact
        # about the model, not about the panel.
        "counts": asdict(catalog.counts()),
        # Defined in the model but absent from what Cube serves — a file that did not compile.
        "unloaded": sorted(
            {
                entity.file
                for entity in defined.values()
                if entity.name not in {served.name for served in catalog.entities}
            }
        ),
        "entities": [
            {
                "name": entity.name,
                "file": defined[entity.name].file if entity.name in defined else "",
                "is_view": entity.is_view,
                "title": entity.title,
                "description": entity.description,
                "ai_context": entity.ai_context,
                "folders": [asdict(folder) for folder in entity.folders],
                "draws_from": catalog.draws_from(entity),
                "exposed_by": catalog.exposed_by(entity),
                "members": [
                    {
                        "id": member.id,
                        "name": member.id.rpartition(".")[2],
                        "kind": member.kind,
                        "type": member.type,
                        "title": member.title,
                        "description": member.description,
                        "ai_context": member.ai_context,
                        "granularities": member.granularities,
                        "operators": member.valid_operators,
                        "alias": member.alias_member,
                        "exposed_by": (
                            [] if entity.is_view else catalog.exposure.get(member.id, [])
                        ),
                    }
                    for member in entity.members
                ],
            }
            for entity in catalog.entities
        ],
    }


@router.get("/model/summary")
async def summary(request: Request, refresh: bool = False) -> dict:
    """One line per entity, for the chat page: the example questions and the header's link.

    The same object `GET /api/model` returns in full — cut down because a transcript needs the
    shape of the model and the data page needs every member of it."""
    cube = await state.cube(request.app)
    catalog = await cube.catalog(refresh=refresh)
    return {
        "cube_url": cube.root,
        "entities": [
            {
                "name": entity.name,
                "view": entity.is_view,
                "members": len(entity.members),
                "description": entity.description,
            }
            for entity in catalog.entities
        ],
    }


# --- refs ---------------------------------------------------------------------


@router.get("/refs")
async def list_refs() -> dict:
    async with controller.store() as store:
        out = []
        for ref in await store.refs():
            name = ref.name
            # Files changed, not commits: it is what a reviewer is about to read.
            changed = 0 if name == MAIN else len((await store.diff(name)).get("changed") or [])
            out.append({"name": name, "changed": changed})
    return {"main": MAIN, "refs": out}


@router.post("/refs")
async def create_ref(body: NewRef, request: Request) -> dict:
    """Branch off an existing ref — main by default, and any other ref on request.

    The source is checked here rather than left to the store, which answers a ref it has never
    heard of with the same 502 as being unreachable: a name picked from a stale dropdown is a bad
    request, not the store failing.

    Whatever it starts from, it can only ever land back on main — the store fast-forwards into
    main and refuses every other target — so branching off a branch carries that branch's commits
    into main with it when it merges. That is the caller's business to know, not this route's to
    prevent.
    """
    state.idle(request.app)
    async with controller.store() as store:
        taken = [ref.name for ref in await store.refs()]
        if error := workspace.validate_ref(body.name, taken):
            raise HTTPException(400, error)
        if body.from_ref not in taken:
            raise HTTPException(400, f"no ref named {body.from_ref!r} to branch from")
        await store.create_ref(body.name, body.from_ref)
    return await list_refs()


@router.delete("/refs/{ref}")
async def delete_ref(ref: str, request: Request) -> dict:
    """Drop the ref and the working copy of it. 404 when there was no such ref."""
    state.idle(request.app)
    controller.editable(ref)
    try:
        async with await open_store() as store:
            await workspace.forget(store, ref)
    except StoreError as exc:
        # `missing` is the one worth telling apart: a name that was never there is a bad request,
        # not the store failing.
        raise HTTPException(404 if exc.missing else 502, str(exc)) from None
    return await list_refs()


@router.get("/refs/{ref}/diff")
async def ref_diff(ref: str) -> dict:
    async with controller.store() as store:
        diff = await store.diff(ref)
    # Every ref is compiled by the same Cube, so impact is answerable for any of them — there is
    # no "check it out first" left.
    report = await controller.broken_by(ref) if ref != MAIN else None
    return {
        "ref": ref,
        "changed": diff.get("changed") or [],
        "patch": diff.get("patch") or "",
        "impact": report and asdict(report),
    }


@router.post("/refs/{ref}/merge")
async def merge_ref(
    ref: str, request: Request, force: bool = False, delete_after: bool = False
) -> dict:
    """Land the ref on main, and optionally delete it.

    There is no separate deploy step: main is what everything but the editor reads, and Cube is
    serving the merged model within seconds.

    Refused when the merge would break a dashboard tile, unless `force`.
    Measured while the ref is still its own — afterwards main *is* the change, and there is
    nothing left to refuse.

    Fast-forward only at the store, so `workspace.land` rebases — or replays the ref onto a fresh
    branch off main — when main has moved under it. `how` says which happened.

    `delete_after` is best-effort on purpose. The merge has landed by then, so a failed cleanup
    is a stale name in a dropdown, and raising would report a merge that worked as one that did
    not.
    """
    state.idle(request.app)
    controller.editable(ref)
    report = await controller.broken_by(ref)
    if not report.ok and not force:
        raise HTTPException(409, report.as_message())
    try:
        async with await open_store() as store:
            how = await workspace.land(store, ref)
            deleted = False
            if delete_after:
                # Best-effort: the merge has landed, so a failed cleanup is a stale name in a
                # dropdown and never a merge reported as failed.
                with suppress(StoreError):
                    await workspace.forget(store, ref)
                    deleted = True
        state.reloaded(request.app)
        # Main's own compile, after the merge: two clean refs can merge into a model that does
        # not compile, and this is the moment anyone would want to know.
        validation = await publish.check()
        return {
            "merged": ref,
            "how": how,
            "deleted": deleted,
            "impact": asdict(report),
            "compiled": validation.compiled,
            "compile_error": validation.compile_error,
            "entities": validation.entities,
            "member_errors": validation.member_errors,
        }
    except StoreError as exc:
        raise HTTPException(409, f"could not merge {ref}: {exc}") from None


# --- files --------------------------------------------------------------------


@router.get("/files")
async def list_files(ref: str = MAIN) -> dict:
    """Every model file the editor may open on this ref, and what the draft has done to them.

    Checks the ref out if it has never been opened here. `dirty` and `deleted` are the draft
    against its origin, computed on this side rather than in the browser: the server owns the
    bytes now, so two answers to "what have I changed" would be one too many.
    """
    async with controller.store() as store:
        await workspace.ensure(store, ref)
    pending = drafts.changes(ref)
    origin = drafts.origin_at(ref)
    return {
        "ref": ref,
        "editable": ref != MAIN,
        "files": sorted(path for path in drafts.files(ref) if workspace.resolve(path)[0]),
        "dirty": sorted(pending.write),
        "deleted": sorted(pending.delete),
        "rev": origin.rev if origin else 0,
    }


@router.get("/files/{path:path}")
async def read_file(path: str, ref: str = MAIN) -> dict:
    """One model file as the draft holds it — including edits not yet published."""
    relative, refused = workspace.resolve(path)
    if not relative:
        raise HTTPException(400, refused)
    async with controller.store() as store:
        await workspace.ensure(store, ref)
    text = drafts.read(ref, relative)
    if text is None:
        raise HTTPException(404, f"no model file at {relative!r} on {ref!r}")
    return {"path": relative, "ref": ref, "text": text}


@router.put("/files/{path:path}")
async def save_file(path: str, body: FileBody) -> dict:
    """Write one file into the ref's draft. Reaches no deployment and compiles nothing.

    Refused on main and on a path Cube would ignore — the same two rules a publish enforces, run
    here so a file that could never land cannot be typed into a draft either.
    """
    controller.editable(body.ref)
    relative, refused = workspace.resolve(path)
    if not relative:
        raise HTTPException(400, refused)
    async with controller.store() as store:
        await workspace.ensure(store, body.ref)
    drafts.save(body.ref, relative, body.text)
    return {"ref": body.ref, "path": relative, "dirty": sorted(drafts.changes(body.ref).write)}


@router.delete("/files/{path:path}")
async def delete_file(path: str, ref: str = MAIN) -> dict:
    """Drop one file from the ref's draft. 404 when it was not there to drop."""
    controller.editable(ref)
    relative, refused = workspace.resolve(path)
    if not relative:
        raise HTTPException(400, refused)
    async with controller.store() as store:
        await workspace.ensure(store, ref)
    if not drafts.remove(ref, relative):
        raise HTTPException(404, f"no model file at {relative!r} on {ref!r}")
    return {"ref": ref, "path": relative, "deleted": sorted(drafts.changes(ref).delete)}


@router.post("/discard")
async def discard_draft(body: Draft) -> dict:
    """Put the draft back to what the store last handed over — one file, or all of it."""
    controller.editable(body.ref)
    if body.path:
        relative, refused = workspace.resolve(body.path)
        if not relative:
            raise HTTPException(400, refused)
        drafts.discard(body.ref, relative)
    else:
        drafts.discard(body.ref)
    return await list_files(body.ref)


@router.post("/check")
async def check_changes(body: Draft) -> dict:
    """The editor's Save: would Cube accept this draft? Nothing is published.

    **Answers 200 for a model the gate or Cube refuses.** A rejection is the answer to the
    question, not a failed request — the editor renders the reasons in one banner, and splitting
    them across a 400 and a 200 would make the button read two code paths to draw one message.
    Only a bad *request* is a status code: main is not editable, a path is not a model file, a
    delete names nothing, or the store is unreachable.

    A clean draft validates the ref exactly as it is committed, which is what the Validate action
    posts.
    """
    async with controller.store() as store:
        verdict = await workspace.verify(store, body.ref, saved=await boards.saved_queries())
    if verdict.kind and verdict.kind != "rejected":
        raise HTTPException(STATUS[verdict.kind], verdict.reason)
    return {
        "ref": body.ref,
        "ok": verdict.ok,
        "errors": verdict.errors,
        "warnings": verdict.warnings,
        "compiled": verdict.compiled,
        "compile_error": verdict.compile_error,
        "member_errors": verdict.member_errors,
        "impact": verdict.broken and asdict(verdict.broken),
    }


@router.post("/commit")
async def commit_changes(body: Draft, request: Request) -> dict:
    """Publish the ref's draft as one commit. This is the deploy.

    The gate runs again inside `workspace.publish` rather than trusting the verdict `check` gave:
    a draft is a file on disk and can be edited between the two, and there is no restart
    afterwards to hide behind — Cube asks the store for this ref's revision on every request.

    Every refusal is a status code here, unlike `check`. This one is a write that did not happen.
    """
    async with controller.store() as store:
        outcome = await workspace.publish(store, body.ref, body.message)
    if not outcome.ok:
        raise HTTPException(STATUS[outcome.kind], outcome.reason)
    # Only the portal's cached health probe is stale — there is nothing to restart.
    state.reloaded(request.app)
    return {
        "ref": body.ref,
        "written": sorted(outcome.write),
        "deleted": sorted(outcome.delete),
        "warnings": outcome.warnings,
    }


# --- the editor chat -----------------------------------------------------------


@router.post("/chats")
async def create_edit_chat(body: Message) -> dict:
    ref = controller.editable(body.ref)
    chat = await transcript.create(f"Editing {ref}", "model", ref)
    return {"id": chat.id, "title": chat.title, "ref": ref}


@router.post("/chats/{chat_id}/messages")
async def send_edit_message(chat_id: str, body: Question, request: Request) -> StreamingResponse:
    chat = await transcript.get(chat_id)
    if chat is None or chat.kind != "model":
        raise HTTPException(404, f"no model chat {chat_id!r}")

    return turn_stream(
        request,
        chat_id,
        lambda cancel: stream_edit_turn(chat_id, body.question, request.app.state.saver, cancel),
    )
