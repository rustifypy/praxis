"""Whether something other than the portal may call the agent's read-only tools, and with what.

One row, like `deployment`: a flag and a token. It lives here rather than beside the MCP routes
because it *is* a row — `web/` was the only tier doing SQLAlchemy, which made it the only tier
that could not be read without knowing the schema.

Off is the meaningful state: the token opens the warehouse to whatever holds it. The row is created
with a fresh token the first time it is asked for, so a fresh install has one to show rather than a
blank panel and a button.

**Rotating is destructive on purpose.** It invalidates every config already pasted anywhere, which
is how access is taken back — the panel says so before the button is pressed.
"""

import secrets
import time
from dataclasses import dataclass

from sqlalchemy import insert, select, update

from .config import settings
from .db import connect
from .db import mcp as row


@dataclass
class Access:
    """The flag and the token, as the request guard sees them.

    Held on `app.state` and written through, so the guard tests one in-memory copy per request
    rather than opening the database on every tool call.
    """

    enabled: bool
    token: str


async def load() -> Access:
    """The stored access, creating it with a fresh token if this install has none.

    Callers must run after `db.create_tables()`, which is what creates the table — there is no
    migration mechanism, so this cannot create it lazily and be sure of the schema.
    """
    async with connect(settings.db_path) as db:
        found = await db.execute(select(row.c.enabled, row.c.token).where(row.c.id == 1))
        stored = found.mappings().first()
        if stored is not None:
            return Access(enabled=bool(stored["enabled"]), token=stored["token"])
        access = Access(enabled=True, token=secrets.token_urlsafe(24))
        now = time.time()
        await db.execute(
            insert(row).values(
                id=1,
                enabled=int(access.enabled),
                token=access.token,
                created_at=now,
                updated_at=now,
            )
        )
        return access


async def save(access: Access) -> None:
    """Write the in-memory copy back. Never reads: the caller holds the truth for this process."""
    async with connect(settings.db_path) as db:
        await db.execute(
            update(row)
            .where(row.c.id == 1)
            .values(enabled=int(access.enabled), token=access.token, updated_at=time.time())
        )
