"""How this process opens SQLite, in one place — so two stores in separate files cannot
drift on a pragma one of them relies on.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


@asynccontextmanager
async def connect(path: Path):
    """One connection, rows as mappings, foreign keys enforced. Creates the parent directory,
    which may not exist on a first run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
