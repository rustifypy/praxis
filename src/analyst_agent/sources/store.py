"""sources.db — the registry, credentials included. The schema is in `db.py`, with every
other table this process creates."""

import json
import time
from pathlib import Path

import aiosqlite

from ..config import settings
from ..db import SOURCES_SCHEMA, connect
from .registry import Source

STEPS = ("connect", "cubes", "describe", "views")
"""In order. The UI renders one row per step, and a step may only run once the one
before it has succeeded."""

STAGES = ("connect", "read", "cubes", "describe", "views")
"""Every action a person can run, in order. `read` is the odd one — it records no step row,
so its state is whether a catalog is cached, and nothing gates it."""


class SourceStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.sources_db_path

    def _db(self):
        return connect(self.path)

    async def init(self) -> None:
        async with self._db() as db:
            await db.executescript(SOURCES_SCHEMA)
            await db.commit()
        # Database passwords in plaintext, so owner-only — the same bar the per-source
        # credential files this replaced were held to.
        self.path.chmod(0o600)

    # --- rows ---------------------------------------------------------------

    async def add(self, source: Source) -> None:
        now = time.time()
        async with self._db() as db:
            await db.execute(
                "INSERT INTO sources (name, description, driver, port, credentials, mounts,"
                " steps, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)",
                (
                    source.name,
                    source.description,
                    source.driver,
                    source.port,
                    json.dumps(source.credentials),
                    json.dumps(source.mounts),
                    now,
                    now,
                ),
            )
            await db.commit()

    async def all(self) -> list[Source]:
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM sources ORDER BY created_at")
        return [_source(r) for r in rows]

    async def get(self, name: str) -> Source | None:
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT * FROM sources WHERE name = ?", (name,))
        return _source(rows[0]) if rows else None

    async def names(self) -> list[str]:
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT name FROM sources ORDER BY created_at")
        return [r["name"] for r in rows]

    async def all_catalog_read(self) -> dict[str, bool]:
        """Whether each source has a cached catalog — `read`'s state, which records no step."""
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT name, state FROM sources")
        return {r["name"]: bool(json.loads(r["state"]).get("catalog")) for r in rows}

    async def all_steps(self) -> dict[str, dict]:
        """Every source's step records, in one read."""
        async with self._db() as db:
            rows = await db.execute_fetchall("SELECT name, steps FROM sources")
        return {r["name"]: json.loads(r["steps"]) for r in rows}

    async def update(
        self,
        name: str,
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
                f"UPDATE sources SET {', '.join(sets)}, updated_at = ? WHERE name = ?",
                (*values, time.time(), name),
            )
            await db.commit()

    async def remove(self, name: str) -> None:
        async with self._db() as db:
            await db.execute("DELETE FROM sources WHERE name = ?", (name,))
            await db.commit()

    # --- the JSON columns ---------------------------------------------------

    async def steps(self, name: str) -> dict:
        return await self._blob(name, "steps")

    async def save_step(self, name: str, step: str, payload: dict) -> None:
        """Replace one step's record, leaving the other three untouched."""
        if step not in STEPS:
            raise ValueError(f"unknown step {step!r} — one of {', '.join(STEPS)}")
        current = await self.steps(name)
        current[step] = payload
        await self._save_blob(name, "steps", current)

    async def clear_steps_after(self, name: str, step: str) -> list[str]:
        """Forget the steps that depend on `step`, and say which were forgotten."""
        current = await self.steps(name)
        dropped = invalidated_by(current, step)
        for s in dropped:
            current.pop(s)
        if dropped:
            await self._save_blob(name, "steps", current)
        return dropped

    async def state(self, name: str) -> dict:
        """The cached catalog, cube bodies and enrichment decisions."""
        return await self._blob(name, "state")

    async def save_state(self, name: str, patch: dict) -> None:
        """Merge `patch` into the stored state at the top level."""
        current = await self.state(name)
        current.update(patch)
        await self._save_blob(name, "state", current)

    async def _blob(self, name: str, column: str) -> dict:
        async with self._db() as db:
            rows = await db.execute_fetchall(
                f"SELECT {column} FROM sources WHERE name = ?", (name,)
            )
        return json.loads(rows[0][column]) if rows else {}

    async def _save_blob(self, name: str, column: str, value: dict) -> None:
        async with self._db() as db:
            await db.execute(
                f"UPDATE sources SET {column} = ?, updated_at = ? WHERE name = ?",
                (json.dumps(value, default=str), time.time(), name),
            )
            await db.commit()


def _source(row: aiosqlite.Row) -> Source:
    return Source(
        name=row["name"],
        description=row["description"],
        driver=row["driver"],
        port=int(row["port"]),
        mounts=json.loads(row["mounts"]),
        credentials=json.loads(row["credentials"]),
    )


def invalidated_by(steps: dict, step: str) -> list[str]:
    """Which recorded steps re-running `step` would throw away, in order."""
    return [s for s in STEPS[STEPS.index(step) + 1 :] if steps.get(s, {}).get("status")]


def blocked_by(steps: dict, stage: str) -> str:
    """The step that must succeed before `stage` may run, or "". `run.action` enforces this and
    the browser explains it, from this one answer — they disagreed about `cubes`, which needs
    `connect` and not `read` because `cubes` reads the catalog itself when none is cached."""
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
    """A source as the browser may see it — every field except the credentials."""
    rail = stages(steps, catalog_read)
    return {
        "name": source.name,
        "description": source.description,
        "driver": source.driver,
        "port": source.port,
        "url": source.url,
        "container": source.service,
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
        # name different next steps for the same source.
        "next_stage": next((s["name"] for s in rail if s["state"] != "done"), None),
        "active": source.is_active,
    }


def merge_credentials(existing: dict[str, str], supplied: dict[str, str]) -> dict[str, str]:
    """Apply an edit. A key the caller left blank keeps its stored value."""
    out = dict(existing)
    for key, value in supplied.items():
        if value != "":
            out[key] = value
    return out
