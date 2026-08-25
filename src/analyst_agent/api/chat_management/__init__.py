"""Chats as things you own: make one, find them, retitle one, throw one away.

Separate from `chat/` on purpose. That package is a chat being *used* — a turn streaming, a file
landing, a result paged through. This one is the list beside it, and the two change for entirely
different reasons.

Deleting is the one that reaches past the row: a chat's events and results go with it, and the
sandbox is asked to drop its workspace. That last part is best-effort — the chat is already gone,
so a sandbox that did not answer must not report this as failed.
"""

from .routes import router

__all__ = ["router"]
