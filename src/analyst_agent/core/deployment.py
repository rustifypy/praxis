"""The one connection this portal has, as a row and as a dataclass.

Functions and not a store class: the only state one held was a path, every caller in the app
used the default, and nothing ever substituted a double for it. `id = 1` is enforced by a CHECK
rather than by convention — there is one deployment, so there is one row.
"""

import time
from dataclasses import dataclass, field, fields

import sqlalchemy as sa

from . import db

ROW = 1
MAIN = "main"

# The sandbox's contract. The model store is a separate product and reports none.
CONTRACT = 1


@dataclass
class Deployment:
    store_url: str = ""
    store_token: str = ""
    api_secret: str = ""
    sandbox_url: str = ""
    sandbox_token: str = ""
    driver: str = "postgres"
    description: str = ""
    credentials: dict[str, str] = field(default_factory=dict)

    @property
    def deployed(self) -> bool:
        return bool(self.store_url)

    # There is no `onboarded` here. Setup now finishes with a built model, which is a fact about
    # the model store rather than about this row — `evidence.onboarded` asks both questions, and
    # a column could not hold the answer anyway: `CREATE TABLE IF NOT EXISTS` does not diff.


async def current() -> Deployment | None:
    async with db.connect() as conn:
        found = await conn.execute(sa.select(db.deployment).where(db.deployment.c.id == ROW))
    stored = found.mappings().first()
    if stored is None:
        return None
    # By name, so a column the dataclass does not carry — `steps`, `state`, the timestamps — is
    # left where it is rather than arriving as an unexpected argument.
    return Deployment(**{f.name: stored[f.name] for f in fields(Deployment)})


async def save(connection: Deployment) -> None:
    # A replacement, not an addition: `steps` and `state` describe the tables of a database that
    # is no longer connected, so they go back to empty with it.
    now = time.time()
    async with db.connect() as conn:
        await conn.execute(sa.delete(db.deployment).where(db.deployment.c.id == ROW))
        await conn.execute(
            sa.insert(db.deployment).values(
                id=ROW,
                **connection.__dict__,
                steps={},
                state={},
                created_at=now,
                updated_at=now,
            )
        )


async def patch(
    *,
    store_url: str | None = None,
    store_token: str | None = None,
    api_secret: str | None = None,
    sandbox_url: str | None = None,
    sandbox_token: str | None = None,
    description: str | None = None,
    credentials: dict[str, str] | None = None,
) -> None:
    # Patch semantics: None leaves a column alone. Not `update`, which is SQLAlchemy's.
    changed = {
        column: value
        for column, value in (
            ("store_url", store_url),
            ("store_token", store_token),
            ("api_secret", api_secret),
            ("sandbox_url", sandbox_url),
            ("sandbox_token", sandbox_token),
            ("description", description),
            ("credentials", credentials),
        )
        if value is not None
    }
    if changed:
        await _write(changed)


async def steps() -> dict:
    return await _blob("steps")


async def save_step(step: str, payload: dict) -> None:
    current = await steps()
    current[step] = payload
    await _write({"steps": current})


async def state() -> dict:
    return await _blob("state")


async def save_state(patched: dict) -> None:
    current = await state()
    current.update(patched)
    await _write({"state": current})


async def _blob(column: str) -> dict:
    async with db.connect() as conn:
        found = await conn.execute(
            sa.select(db.deployment.c[column]).where(db.deployment.c.id == ROW)
        )
    return found.scalar_one_or_none() or {}


async def _write(changed: dict) -> None:
    # Every write to the row goes through here, so `updated_at` cannot be forgotten by one.
    async with db.connect() as conn:
        await conn.execute(
            sa.update(db.deployment)
            .where(db.deployment.c.id == ROW)
            .values(**changed, updated_at=time.time())
        )
