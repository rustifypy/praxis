"""The four chat-management routes.

They share `/api/chats` with `chat/`. Nothing here takes a path parameter deeper than
`/{chat_id}`, so no route here can shadow one there whichever router loads first —
`test_every_route_is_reachable_and_unshadowed` is what says so.
"""

from fastapi import APIRouter

from ...core.transcript import Chat
from . import controller
from .dto import NewChat, Ok, Rename

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
    """Delete a chat, its events and results, and ask the sandbox to drop its workspace."""
    await controller.delete(chat_id)
    return Ok()
