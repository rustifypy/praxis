"""Question chats: the list, the transcript, and one turn as a stream.

Model chats are `web/model.py`'s — they share this table and the event format and differ only in
`kind`, so `list_chats` filters and the two surfaces stay apart.
"""

import asyncio
import contextlib
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import transcript
from ..agent.turn import stream_turn
from ..core.config import settings
from ..sandbox import SandboxError, open_sandbox
from . import state
from .stream import turn_stream

router = APIRouter(prefix="/api/chats")


class NewChat(BaseModel):
    title: str = "New chat"


class Rename(BaseModel):
    title: str


class Decision(BaseModel):
    decision: Literal["yes", "always", "no"] = "yes"


class Question(BaseModel):
    question: str
    # Workspace paths the composer attached. The user event carries them, which is how an
    # upload reaches the transcript at all.
    files: list[str] = []


@router.get("")
async def list_chats() -> list[dict]:
    chats = await transcript.list_chats()
    return [chat.__dict__ for chat in chats]


@router.post("")
async def create_chat(body: NewChat) -> dict:
    chat = await transcript.create(body.title)
    return chat.__dict__


@router.patch("/{chat_id}")
async def rename_chat(chat_id: str, body: Rename) -> dict:
    await transcript.rename(chat_id, body.title)
    return {"ok": True}


@router.delete("/{chat_id}")
async def delete_chat(chat_id: str) -> dict:
    # Takes the workspace with it, best-effort: a sandbox that is not answering must not leave
    # a chat undeletable.
    await transcript.delete_chat(chat_id)
    with contextlib.suppress(SandboxError):
        async with await open_sandbox() as sandbox:
            await sandbox.forget(chat_id)
    return {"ok": True}


@router.get("/{chat_id}/events")
async def chat_events(chat_id: str) -> list[dict]:
    return await transcript.events(chat_id)


@router.post("/{chat_id}/stop")
async def stop(chat_id: str, request: Request) -> dict:
    cancel = request.app.state.cancels.get(chat_id)
    if cancel:
        cancel.set()
    return {"stopped": bool(cancel)}


@router.post("/{chat_id}/approve")
async def approve(chat_id: str, body: Decision, request: Request) -> dict:
    # "always" also stops asking for the rest of the turn: the tool reads that off the same
    # answer, since it holds the queue and this route does not.
    queue = request.app.state.approvals.get(chat_id)
    if queue is None:
        raise HTTPException(404, "nothing in this chat is waiting to be approved")
    if body.decision == "always":
        request.app.state.always.add(chat_id)
    queue.put_nowait(body.decision)
    return {"decision": body.decision}


@router.post("/{chat_id}/messages")
async def send(chat_id: str, body: Question, request: Request) -> StreamingResponse:
    chat = await transcript.get(chat_id)
    if chat is None:
        raise HTTPException(404, "no such chat")

    # The first question names the chat. Cheap, deterministic, no extra model call.
    if chat.title == "New chat":
        await transcript.rename(chat_id, body.question[:60])

    # Before anything is put on `app.state`: this is the one step here that refuses, and a queue
    # registered ahead of it would outlive a turn that never started — nothing would pop it,
    # since the stream whose `finally` does that is never opened.
    cube = await state.cube(request.app)

    # Only files actually in the workspace, so the transcript cannot name one that is not there.
    # An unreachable sandbox attaches nothing rather than trusting the browser's list.
    known: list[str] = []
    with contextlib.suppress(SandboxError):
        async with await open_sandbox() as sandbox:
            known, _ = await sandbox.listing(chat_id)
    attached = [path for path in body.files if path in known]

    approvals = None
    if settings.approve_scripts and chat_id not in request.app.state.always:
        approvals = asyncio.Queue()
        request.app.state.approvals[chat_id] = approvals

    return turn_stream(
        request,
        chat_id,
        lambda cancel: stream_turn(
            chat_id, body.question, request.app.state.saver, cube, cancel, attached, approvals
        ),
    )
