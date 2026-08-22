"""The model: what Cube serves, the grounding beside it, and the refs it is edited on.

One noun, four sections — what is served, refs, files, and the editor's own chat. They are one
module because they are one prefix and one thing: `GET /api/model` is what the data page renders
and `PUT /api/model/files/{path}` is what changes it, and a reader looking for either should not
have to guess which file it is in.

Editing invariants live in `datamodel/editing/`; the two that constrain *these routes* are that
the gate runs before the write (because the write is the deploy) and that a workspace failure is
an `Outcome` rather than an exception — one escaping here would 500 the route.
"""

from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import transcript
from ..agent.turn import stream_edit_turn
from ..core import deployment as connection
from ..core.deployment import MAIN
from ..cube.client import CubeError, served_catalog
from ..datamodel import definitions, grounding
from ..datamodel.editing import impact, workspace
from ..datamodel.modelgen import publish
from ..modelstore import StoreError, model_files
from ..modelstore.client import open_store
from . import state
from .stream import turn_stream

router = APIRouter(prefix="/api/model")


class NewRef(BaseModel):
    name: str


class Message(BaseModel):
    ref: str = MAIN


class FileBody(BaseModel):
    text: str


# --- shared -------------------------------------------------------------------


# The kinds are the workspace's; the codes are HTTP's.
STATUS = {"ref": 409, "path": 400, "missing": 404, "rejected": 400, "store": 502}


def _editable(ref: str) -> str:
    # One rule, in `workspace`, so the routes and the editor's tools cannot disagree about what
    # is editable.
    if refusal := workspace.editable(ref):
        raise HTTPException(STATUS[refusal.kind], refusal.reason)
    return ref


@asynccontextmanager
async def _store():
    # What a store failure becomes as HTTP, in one place. `open_store` raises when nothing is
    # deployed, so the two routes that used to open it bare answered 500 for a portal that
    # simply has no store yet. The two callers wanting a different code keep their own except.
    try:
        async with await open_store() as store:
            yield store
    except StoreError as exc:
        raise HTTPException(502, str(exc)) from None


async def _impact(ref: str) -> impact.Impact:
    # Measured against the ref actually being asked about. One Cube compiles every ref
    # independently, so this is the model Cube would answer with for that ref — not a
    # projection of one.
    try:
        catalog = await served_catalog(ref=ref)
        grounded = await grounding.load(ref)
        return await impact.check(catalog, grounded)
    except (CubeError, StoreError) as exc:
        raise HTTPException(409, f"cannot read the model to check impact: {exc}") from None


def _validation(validation: publish.Validation) -> dict:
    return {
        "compiled": validation.compiled,
        "compile_error": validation.compile_error,
        "entities": validation.entities,
        "member_errors": validation.member_errors,
    }


# --- what is served -----------------------------------------------------------


@router.get("")
async def served_model() -> dict:
    # Which file defines each entity, so the panel that lists what Cube serves is also the one
    # that edits it. Keyed by name and read from the store rather than assumed from
    # `cubes/<name>.yml`: a hand-edited model may put a cube anywhere.
    try:
        stored = await model_files()
    except StoreError:
        # One of the two halves being unreachable must not hide the other: without the store
        # nothing names the file an entity came from, and Cube can still say what it serves.
        stored = {}
    defined, _ = definitions.read(stored)

    try:
        catalog = await served_catalog()
    except CubeError as exc:
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


@router.get("/grounding")
async def model_grounding() -> dict:
    # Main, since that is the ref a question chat is pinned to.
    deployment = await connection.current()
    root = deployment.store_url if deployment else ""
    try:
        loaded = await grounding.load()
    except StoreError:
        # Reports empty against the address rather than 500ing the whole data page.
        return {"root": root, "exists": False, "artefacts": []}
    kinds = (
        ("rule", loaded.rules),
        ("skill", loaded.skills),
        ("certified", loaded.certified),
    )
    return {
        "root": root,
        "exists": True,
        "artefacts": [
            {
                "kind": kind,
                "name": artefact.name,
                "file": artefact.file,
                "description": artefact.description,
                # Rules only, defaulted the way `always_rules` does: an untyped rule loads
                # every turn, which is the expensive half.
                "loading": artefact.meta.get("type", "always") if kind == "rule" else "",
                "user_request": artefact.meta.get("user_request", ""),
                "body": artefact.body,
            }
            for kind, artefacts in kinds
            for artefact in artefacts
        ],
    }


@router.get("/summary")
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
    async with _store() as store:
        out = []
        for ref in await store.refs():
            name = ref["name"]
            # Files changed, not commits: it is what a reviewer is about to read.
            changed = 0 if name == MAIN else len((await store.diff(name)).get("changed") or [])
            out.append({"name": name, "changed": changed})
    return {"main": MAIN, "refs": out}


@router.post("/refs")
async def create_ref(body: NewRef, request: Request) -> dict:
    state.idle(request.app)
    async with _store() as store:
        taken = [ref["name"] for ref in await store.refs()]
        if error := workspace.validate_ref(body.name, taken):
            raise HTTPException(400, error)
        await store.create_ref(body.name, MAIN)
    return await list_refs()


@router.delete("/refs/{ref}")
async def delete_ref(ref: str, request: Request) -> dict:
    state.idle(request.app)
    _editable(ref)
    async with _store() as store:
        await store.delete_ref(ref)
    return await list_refs()


@router.get("/refs/{ref}/diff")
async def ref_diff(ref: str) -> dict:
    async with _store() as store:
        diff = await store.diff(ref)
    # Every ref is compiled by the same Cube, so impact is answerable for any of them — there is
    # no "check it out first" left.
    report = await _impact(ref) if ref != MAIN else None
    return {
        "ref": ref,
        "changed": diff.get("changed") or [],
        "patch": diff.get("patch") or "",
        "impact": report and asdict(report),
    }


@router.post("/refs/{ref}/merge")
async def merge_ref(ref: str, request: Request, force: bool = False) -> dict:
    # Lands the ref on main. There is no separate deploy step.
    state.idle(request.app)
    _editable(ref)
    # While it is still its own ref. Afterwards there is nothing to refuse.
    report = await _impact(ref)
    if not report.ok and not force:
        raise HTTPException(409, report.as_message())
    try:
        async with await open_store() as store:
            await store.merge(ref, MAIN)
        state.reloaded(request.app)
        return {
            "merged": ref,
            "impact": asdict(report),
            **_validation(await publish.check()),
        }
    except StoreError as exc:
        raise HTTPException(409, f"could not merge {ref}: {exc}") from None


# --- files --------------------------------------------------------------------


@router.get("/files")
async def list_files(ref: str = MAIN) -> dict:
    async with _store() as store:
        files = workspace.model_yaml(await store.files(ref, refresh=True))
    return {"ref": ref, "editable": ref != MAIN, "files": sorted(files)}


@router.get("/files/{path:path}")
async def read_file(path: str, ref: str = MAIN) -> dict:
    try:
        relative, refused = workspace.resolve(path)
        if not relative:
            raise HTTPException(400, refused)
        async with await open_store() as store:
            text = await store.file(relative, ref)
        return {"path": relative, "ref": ref, "text": text}
    except StoreError as exc:
        raise HTTPException(404 if exc.missing else 502, str(exc)) from None


@router.put("/files/{path:path}")
async def write_file(path: str, body: FileBody, ref: str = MAIN) -> dict:
    # Tiers 1-2, then the write: what the gate refuses is never committed.
    async with _store() as store:
        outcome = await workspace.write(store, ref, path, body.text)
    if not outcome.ok:
        raise HTTPException(STATUS[outcome.kind], outcome.reason)
    return {"path": outcome.path, "ref": ref, "warnings": outcome.warnings}


@router.delete("/files/{path:path}")
async def delete_file(path: str, ref: str = MAIN) -> dict:
    async with _store() as store:
        outcome = await workspace.delete(store, ref, path)
    if not outcome.ok:
        raise HTTPException(STATUS[outcome.kind], outcome.reason)
    return {"deleted": outcome.path, "ref": ref}


@router.post("/validate")
async def validate(request: Request, ref: str = MAIN) -> dict:
    # Tier 3 — ask Cube itself.
    state.idle(request.app)
    state.reloaded(request.app)
    validation = await publish.check(ref=ref)
    # Reported here as well as at merge, so a broken tile surfaces while the edit is still
    # under the cursor rather than at the moment someone wanted to land it.
    impacted = await _impact(ref) if validation.compiled else None
    return {"ref": ref, **_validation(validation), "impact": impacted and asdict(impacted)}


# --- the editor chat -----------------------------------------------------------


@router.get("/chats")
async def list_edit_chats() -> list[dict]:
    chats = await transcript.list_chats("model")
    return [chat.__dict__ for chat in chats]


@router.post("/chats")
async def create_edit_chat(body: Message) -> dict:
    ref = _editable(body.ref)
    chat = await transcript.create(f"Editing {ref}", "model", ref)
    return {"id": chat.id, "title": chat.title, "ref": ref}


class Question(BaseModel):
    question: str


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
