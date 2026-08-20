"""The Cube deployment and the model store beside it — one row in `analyst.db`."""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings
from .db import SCHEMA, connect

ROW = 1
MAIN = "main"


@dataclass
class Deployment:
    """One address for the semantic layer and one for the sandbox, plus the credentials the
    deployment is run with. Both are empty until someone has run it.

    There is no Cube address here: Cube is not published and is reached through the store at
    `/cube/...`, which is also the only thing holding a secret that can sign a Cube token. Where
    Cube actually is, is the store's `CUBE_URL` — its business, not this row's.
    """

    store_url: str = ""
    store_token: str = ""
    api_secret: str = ""
    """Cube's, generated here and written into the environment file. It never reaches Cube from
    this process — the store signs with it — so this is a value the portal mints and hands over,
    not one it uses."""
    store_read_token: str = ""
    """Cube's, and read-only. Kept because the environment file is rendered from this row
    whenever it is asked for, not once at connect time."""
    sandbox_url: str = ""
    sandbox_token: str = ""
    """Where the agent runs Python. Optional: a deployment without it answers questions and
    just cannot do arithmetic a `CubeQuery` will not express."""
    driver: str = "postgres"
    description: str = ""
    credentials: dict[str, str] = field(default_factory=dict)
    mounts: dict[str, str] = field(default_factory=dict)

    @property
    def deployed(self) -> bool:
        """Whether anything is actually running yet. The portal does not run it, so this is a
        fact it learns by being told an address rather than one it can arrange. The sandbox is
        not part of it — a deployment without one answers questions perfectly well."""
        return bool(self.store_url)


class DeploymentStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.db_path

    def _db(self):
        return connect(self.path)

    async def init(self) -> None:
        async with self._db() as db:
            await db.executescript(SCHEMA)
            await db.commit()
        # Database passwords in plaintext, so owner-only: the file mode is the whole guard.
        self.path.chmod(0o600)

    async def save(self, deployment: Deployment) -> None:
        """Write the deployment, replacing whatever was there. Steps and state reset with it:
        they describe a database, and this is a different one."""
        now = time.time()
        async with self._db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO deployment (id, store_url, store_token,"
                " store_read_token, api_secret, sandbox_url, sandbox_token, driver, description,"
                " credentials, mounts, steps, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)",
                (
                    ROW,
                    deployment.store_url,
                    deployment.store_token,
                    deployment.store_read_token,
                    deployment.api_secret,
                    deployment.sandbox_url,
                    deployment.sandbox_token,
                    deployment.driver,
                    deployment.description,
                    json.dumps(deployment.credentials),
                    json.dumps(deployment.mounts),
                    now,
                    now,
                ),
            )
            await db.commit()

    async def get(self) -> Deployment | None:
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM deployment WHERE id = ?", (ROW,))
        if not rows:
            return None
        row = rows[0]
        return Deployment(
            store_url=row["store_url"],
            store_token=row["store_token"],
            store_read_token=row["store_read_token"],
            api_secret=row["api_secret"],
            sandbox_url=row["sandbox_url"],
            sandbox_token=row["sandbox_token"],
            driver=row["driver"],
            description=row["description"],
            credentials=json.loads(row["credentials"]),
            mounts=json.loads(row["mounts"]),
        )

    async def update(
        self,
        *,
        store_url: str | None = None,
        store_token: str | None = None,
        store_read_token: str | None = None,
        sandbox_url: str | None = None,
        sandbox_token: str | None = None,
        description: str | None = None,
        credentials: dict[str, str] | None = None,
        mounts: dict[str, str] | None = None,
    ) -> None:
        sets: list[str] = []
        values: list[object] = []
        for column, value in (
            ("store_url", store_url),
            ("store_token", store_token),
            ("store_read_token", store_read_token),
            ("sandbox_url", sandbox_url),
            ("sandbox_token", sandbox_token),
            ("description", description),
            ("credentials", None if credentials is None else json.dumps(credentials)),
            ("mounts", None if mounts is None else json.dumps(mounts)),
        ):
            if value is not None:
                sets.append(f"{column} = ?")
                values.append(value)
        if not sets:
            return
        async with self._db() as db:
            await db.execute(
                f"UPDATE deployment SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                (*values, time.time(), ROW),
            )
            await db.commit()

    async def remove(self) -> None:
        async with self._db() as db:
            await db.execute("DELETE FROM deployment WHERE id = ?", (ROW,))
            await db.commit()

    async def steps(self) -> dict:
        return await self._blob("steps")

    async def save_step(self, step: str, payload: dict) -> None:
        current = await self.steps()
        current[step] = payload
        await self._save_blob("steps", current)

    async def drop_steps(self, names: list[str]) -> None:
        current = await self.steps()
        for name in names:
            current.pop(name, None)
        if names:
            await self._save_blob("steps", current)

    async def state(self) -> dict:
        return await self._blob("state")

    async def save_state(self, patch: dict) -> None:
        current = await self.state()
        current.update(patch)
        await self._save_blob("state", current)

    async def _blob(self, column: str) -> dict:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                f"SELECT {column} FROM deployment WHERE id = ?", (ROW,)
            )
        return json.loads(rows[0][column]) if rows else {}

    async def _save_blob(self, column: str, value: dict) -> None:
        async with self._db() as db:
            await db.execute(
                f"UPDATE deployment SET {column} = ?, updated_at = ? WHERE id = ?",
                (json.dumps(value, default=str), time.time(), ROW),
            )
            await db.commit()


async def current() -> Deployment | None:
    """The deployment, read fresh. Cheap enough to read per use, which is what keeps an edited
    URL from leaving a stale copy behind anywhere."""
    return await DeploymentStore().get()
