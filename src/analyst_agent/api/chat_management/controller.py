"""Listing, creating, retitling and deleting a chat."""

import contextlib

from ...core import transcript
from ...core.transcript import Chat
from ...sandbox import SandboxError, open_sandbox


async def listed() -> list[Chat]:
    return await transcript.list_chats()


async def create(title: str) -> Chat:
    return await transcript.create(title)


async def rename(chat_id: str, title: str) -> None:
    await transcript.rename(chat_id, title)


async def delete(chat_id: str) -> None:
    """Drop the chat, then ask the sandbox to forget its workspace.

    The sandbox failure is suppressed on purpose: the chat is already gone from the transcript, and
    reporting the delete as failed because an optional service did not answer would leave the
    browser showing a row that no longer exists. The workspace is then orphaned, which costs disk
    and nothing else.
    """
    await transcript.delete_chat(chat_id)
    with contextlib.suppress(SandboxError):
        async with await open_sandbox() as sandbox:
            await sandbox.forget(chat_id)
