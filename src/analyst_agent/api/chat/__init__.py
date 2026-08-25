"""Question chats: the list, the transcript, and one turn as a stream.

Model chats are `web/model/`'s. They share this table and the event format and differ only in
`kind`, so listing filters on it and the two surfaces stay apart — one answers questions about the
database, the other edits the model, and merging them would mean one screen with two agents.

**The event log is the transcript.** `GET /{chat_id}/events` returns it verbatim, and a `Session`'s
provenance is rebuilt from it, so nothing here holds state that the log does not already carry.
"""

from .routes import router

__all__ = ["router"]
