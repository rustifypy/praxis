"""The data source — credentials included — in `analyst.db` with everything else. One Cube
deployment, so one row; the schema is in `core/db.py`."""

import json
import time
from pathlib import Path
from typing import Literal, get_args

from pydantic import validate_call

from ..core.config import settings
from ..core.db import SCHEMA, connect
from .docker import CONTAINER
from .registry import Source

Step = Literal["connect", "cubes", "describe", "views"]
"""In order. The UI renders one row per step, and a step may only run once the one
before it has succeeded. Annotating a parameter with this is what refuses an unknown one —
`@validate_call` enforces it, so no function needs to test the tuple itself."""
STEPS: tuple[Step, ...] = get_args(Step)
"""The same names to iterate, index and join. Derived, so the two cannot drift."""

Stage = Literal["connect", "read", "cubes", "describe", "views"]
"""Every action a person can run, in order — the one definition of the workflow. `steps.action`
dispatches on it, `stages` renders it, and the browser iterates the shipped rail rather than
keeping a fourth copy. `read` is the odd one: it records no step row, so its state is whether
a catalog is cached, and nothing gates it."""
STAGES: tuple[Stage, ...] = get_args(Stage)

ROW = 1
"""The only row id there is. `core.db.SCHEMA` enforces it with a CHECK."""


class SourceStore:
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

    # --- the row ------------------------------------------------------------

    async def save(self, source: Source) -> None:
        """Write the connection, replacing whatever was there. Steps and state are reset with
        it: they describe a database, and this is a different one."""
        now = time.time()
        async with self._db() as db:
            await db.execute(
                "INSERT OR REPLACE INTO source (id, description, driver, credentials, mounts,"
                " steps, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '{}', '{}', ?, ?)",
                (
                    ROW,
                    source.description,
                    source.driver,
                    json.dumps(source.credentials),
                    json.dumps(source.mounts),
                    now,
                    now,
                ),
            )
            await db.commit()

    async def get(self) -> Source | None:
        """The connection, or None before one has been made."""
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM source WHERE id = ?", (ROW,))
        if not rows:
            return None
        row = rows[0]
        return Source(
            driver=row["driver"],
            description=row["description"],
            mounts=json.loads(row["mounts"]),
            credentials=json.loads(row["credentials"]),
        )

    async def catalog_read(self) -> bool:
        return has_catalog(await self.state())

    async def update(
        self,
        *,
        description: str | None = None,
        credentials: dict[str, str] | None = None,
        mounts: dict[str, str] | None = None,
    ) -> None:
        """Patch what a person can edit. Omitted arguments are left alone."""
        sets: list[str] = []
        values: list[object] = []
        for column, value in (
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
                f"UPDATE source SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                (*values, time.time(), ROW),
            )
            await db.commit()

    async def remove(self) -> None:
        async with self._db() as db:
            await db.execute("DELETE FROM source WHERE id = ?", (ROW,))
            await db.commit()

    # --- the JSON columns ---------------------------------------------------

    async def steps(self) -> dict:
        return await self._blob("steps")

    @validate_call
    async def save_step(self, step: Step, payload: dict) -> None:
        """Replace one step's record, leaving the other three untouched."""
        current = await self.steps()
        current[step] = payload
        await self._save_blob("steps", current)

    async def clear_steps_after(self, step: str) -> list[str]:
        """Forget the steps that depend on `step`, and say which were forgotten."""
        current = await self.steps()
        dropped = invalidated_by(current, step)
        for s in dropped:
            current.pop(s)
        if dropped:
            await self._save_blob("steps", current)
        return dropped

    async def state(self) -> dict:
        """The cached catalog, cube bodies and enrichment decisions."""
        return await self._blob("state")

    async def save_state(self, patch: dict) -> None:
        """Merge `patch` into the stored state at the top level."""
        current = await self.state()
        current.update(patch)
        await self._save_blob("state", current)

    async def _blob(self, column: str) -> dict:
        async with self._db() as db:
            rows = await db.execute_fetchall(f"SELECT {column} FROM source WHERE id = ?", (ROW,))
        return json.loads(rows[0][column]) if rows else {}

    async def _save_blob(self, column: str, value: dict) -> None:
        async with self._db() as db:
            await db.execute(
                f"UPDATE source SET {column} = ?, updated_at = ? WHERE id = ?",
                (json.dumps(value, default=str), time.time(), ROW),
            )
            await db.commit()


def has_catalog(state: dict) -> bool:
    """`read`'s state. It records no step row, so a cached catalog is the only evidence it ran."""
    return bool(state.get("catalog"))


def invalidated_by(steps: dict, step: str) -> list[str]:
    """Which recorded steps re-running `step` would throw away, in order."""
    return [s for s in STEPS[STEPS.index(step) + 1 :] if steps.get(s, {}).get("status")]


def blocked_by(steps: dict, stage: str) -> str:
    """The step that must succeed before `stage` may run, or "". `steps.action` enforces this and
    the browser explains it, both from this one answer. `cubes` needs `connect` and not `read`,
    because `cubes` reads the catalog itself when none is cached."""
    if stage not in STEPS:
        return ""
    index = STEPS.index(stage)
    previous = STEPS[index - 1] if index else ""
    return previous if previous and steps.get(previous, {}).get("status") != "done" else ""


def stages(steps: dict, catalog_read: bool) -> list[dict]:
    """Where each stage stands and what is holding it up — the whole progress rail, decided
    once here rather than re-derived per component."""
    out = []
    for stage in STAGES:
        status = steps.get(stage, {}).get("status")
        if stage == "read":
            state = "done" if catalog_read else "todo"
        else:
            state = status if status in ("done", "error") else "todo"
        out.append({"name": stage, "state": state, "blocked_by": blocked_by(steps, stage)})
    return out


def public(
    source: Source, steps: dict, running: bool, health: dict, catalog_read: bool = False
) -> dict:
    """The source as the browser may see it — every field except the credentials."""
    rail = stages(steps, catalog_read)
    return {
        "label": source.label,
        "description": source.description,
        "driver": source.driver,
        "url": settings.cube_url,
        "container": CONTAINER,
        # Which connection fields are set, so the edit form can show them filled —
        # names only, never values.
        "credential_keys": sorted(source.credentials),
        # Host paths are not credentials: they are paths on the machine the person is
        # already sitting at, and the edit form has to show the current one because a
        # key file field cannot be prefilled from the credential (which holds the
        # container path, not this).
        "mounts": dict(source.mounts),
        "steps": steps,
        "running": running,
        # {"healthy": bool, "status": str, "detail": str} — structured, so the browser
        # does not have to parse prose to colour a dot.
        "health": health,
        "invalidates": {step: invalidated_by(steps, step) for step in STEPS},
        "stages": rail,
        # The primary action every screen offers. One answer, so a sidebar and a page cannot
        # name different next steps.
        "next_stage": next((s["name"] for s in rail if s["state"] != "done"), None),
    }


def merge_credentials(existing: dict[str, str], supplied: dict[str, str]) -> dict[str, str]:
    """Apply an edit. A key the caller left blank keeps its stored value."""
    out = dict(existing)
    for key, value in supplied.items():
        if value != "":
            out[key] = value
    return out
