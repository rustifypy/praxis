"""A chat in use: one turn as a stream, the files beside it, and the rows it produced.

**The event log is the transcript.** `GET /{chat_id}/events` returns it verbatim, and a `Session`'s
provenance is rebuilt from it, so nothing about a chat lives only in memory.

A workspace and a result set both exist *within* a chat, which is why they are here rather than
in packages of their own: a file is uploaded to a chat, a result is addressed from a chat's
transcript, and neither is reachable or meaningful without one.

Making, naming and deleting a chat is `chat_management/`.
"""

from .routes import results, router

__all__ = ["results", "router"]
