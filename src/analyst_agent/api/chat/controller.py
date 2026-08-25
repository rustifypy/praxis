"""Listing, renaming and deleting chats, and preparing one turn.

`prepare` is the only interesting one: it does everything a turn needs *before* the stream opens,
because once `turn_stream` has the generator there is nowhere left to raise. What it returns is
the three things the agent has to be handed — the Cube session, the attachments that really exist,
and an approval queue if scripts are being approved.

No `HTTPException` here: an unknown chat comes back as `None` and the route decides that is a 404.
"""

import asyncio
import contextlib

from ...core import transcript
from ...core.config import settings
from ...core.transcript import Chat
from ...sandbox import SandboxError, open_sandbox
from ..support import state
from .dto import Decided, Stopped


async def listed() -> list[Chat]:
    """Every question chat, newest first. Model chats are filtered out by kind, not by a table."""
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


async def events(chat_id: str) -> list[dict]:
    """The event log, verbatim. It *is* the transcript — see the package docstring."""
    return await transcript.events(chat_id)


def stop(app, chat_id: str) -> Stopped:
    """Set the cancel flag for a turn in flight, if there is one."""
    cancel = app.state.cancels.get(chat_id)
    if cancel:
        cancel.set()
    return Stopped(stopped=bool(cancel))


def approve(app, chat_id: str, decision: str) -> Decided | None:
    """Answer the script this chat is waiting on, or `None` if nothing is waiting.

    `always` is remembered on `app.state` rather than stored: a restart is a fair moment to start
    asking again.
    """
    queue = app.state.approvals.get(chat_id)
    if queue is None:
        return None
    if decision == "always":
        app.state.always.add(chat_id)
    queue.put_nowait(decision)
    return Decided(decision=decision)


async def prepare(app, chat_id: str, question: str, files: list[str]):
    """Everything a turn needs before its stream opens, or `None` if there is no such chat.

    Returns `(cube, attached, approvals)`. Done here rather than inside the stream because a turn
    that cannot start must fail as a status code — once the generator is handed to `turn_stream`
    there is no way left to report anything but an event, and a refused turn would read as a
    silent one.

    The first question becomes the title, so a chat list is readable without opening anything.

    Attachments are intersected with what the sandbox actually holds: the prompt is built from that
    directory every turn, so naming a file that is not there would describe one the agent cannot
    open.
    """
    chat = await transcript.get(chat_id)
    if chat is None:
        return None
    if chat.title == "New chat":
        await transcript.rename(chat_id, question[:60])

    cube = await state.cube(app)

    known: list[str] = []
    with contextlib.suppress(SandboxError):
        async with await open_sandbox() as sandbox:
            known, _ = await sandbox.listing(chat_id)
    attached = [path for path in files if path in known]

    approvals = None
    if settings.approve_scripts and chat_id not in app.state.always:
        approvals = asyncio.Queue()
        app.state.approvals[chat_id] = approvals

    return cube, attached, approvals
