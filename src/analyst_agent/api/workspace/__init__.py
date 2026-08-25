"""The sandbox boundary: a file in, a file out, and taking one back.

Under `/api/chats` because a workspace belongs to a chat, but nothing here touches the
transcript — the files live in the *service's* volume, which is why none of them has a row. A row
could only ever be a second copy of `ls`, and the copy is what would go stale.

**Not `modelstore/editing/workspace.py`**, which is the door every model edit goes through. This is
the per-chat scratch directory the agent's Python writes into. The two share a word and nothing
else; the wire path says `files` today, and `mvc-migration.md`'s rename #3 is what fixes that.
"""

from .routes import router

__all__ = ["router"]
