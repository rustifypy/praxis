"""How this process opens SQLite, in one place.

Two stores now — chats and sources — deliberately in separate files. How those files are
opened is not a per-store decision, and keeping it here is what stops one of them
quietly missing a pragma the other relies on.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


@asynccontextmanager
async def connect(path: Path):
    """One connection, rows as mappings, foreign keys enforced.

    Creates the parent directory: both databases live under a dot-directory in $HOME
    that may not exist on a first run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
