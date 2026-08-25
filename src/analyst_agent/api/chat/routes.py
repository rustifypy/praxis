"""The eight chat routes.

Order matters within the prefix: every fixed-segment route here is registered before
`web/workspace/`'s `{path:path}` ones, which share `/api/chats`. They do not currently collide —
the greedy parameters sit under a literal `files/` or `preview/` segment — and
`test_every_route_is_reachable_and_unshadowed` is what would say so if that changed.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...agent.turn import stream_turn
from ...core.transcript import Chat
from ..support.stream import turn_stream
from . import controller
from .dto import Decided, Decision, NewChat, Ok, Question, Rename, Stopped

router = APIRouter(prefix="/api/chats")


@router.get("", response_model=list[Chat])
async def list_chats() -> list[Chat]:
    """Every question chat, newest activity first.
    Model chats are absent: they are the same table filtered by kind, and the editor lists its
    own."""
    return await controller.listed()


@router.post("", response_model=Chat)
async def create_chat(body: NewChat) -> Chat:
    """Start an empty chat and return it, so the browser can select it immediately.
    Its title is a placeholder until the first question replaces it."""
    return await controller.create(body.title)


@router.patch("/{chat_id}", response_model=Ok)
async def rename_chat(chat_id: str, body: Rename) -> Ok:
    """Retitle a chat.
    Answers `ok` rather than the row: the browser already has it and renaming cannot fail
    usefully — an unknown id updates nothing."""
    await controller.rename(chat_id, body.title)
    return Ok()


@router.delete("/{chat_id}", response_model=Ok)
async def delete_chat(chat_id: str) -> Ok:
    """Delete a chat, its events and results, and ask the sandbox to drop its workspace.
    The workspace half is best-effort: the chat is already gone, so a sandbox that did not answer
    must not report this as failed."""
    await controller.delete(chat_id)
    return Ok()


@router.get("/{chat_id}/events")
async def read_events(chat_id: str) -> list[dict]:
    """The chat's whole event log, in order — this is the transcript the browser replays.
    No response model: the events are a union whose one definition is `core/events.py`, and
    naming them again here would be a third copy of the wire format."""
    return await controller.events(chat_id)


@router.post("/{chat_id}/stop", response_model=Stopped)
async def stop_turn(chat_id: str, request: Request) -> Stopped:
    """Ask the turn in flight to stop, and say whether there was one.
    Never a 404: a stop that lands after the turn ended has done its job, and an error there
    would put a red banner on a finished answer."""
    return controller.stop(request.app, chat_id)


@router.post("/{chat_id}/approve", response_model=Decided)
async def approve_script(chat_id: str, body: Decision, request: Request) -> Decided:
    """Answer the script this chat is waiting on: yes, no, or always for the rest of the session.
    404 when nothing is waiting — a click that arrives after the turn ended must not read as
    having approved anything."""
    decided = controller.approve(request.app, chat_id, body.decision)
    if decided is None:
        raise HTTPException(404, "nothing in this chat is waiting to be approved")
    return decided


@router.post("/{chat_id}/messages")
async def send_message(chat_id: str, body: Question, request: Request) -> StreamingResponse:
    """One turn of the question agent, streamed as server-sent events.
    404 before the stream opens if there is no such chat: once the generator is handed over there
    is no way left to report anything but an event, and a refused turn would read as a silent
    one."""
    ready = await controller.prepare(request.app, chat_id, body.question, body.files)
    if ready is None:
        raise HTTPException(404, "no such chat")
    cube, attached, approvals = ready

    return turn_stream(
        request,
        chat_id,
        lambda cancel: stream_turn(
            chat_id, body.question, request.app.state.saver, cube, cancel, attached, approvals
        ),
    )
