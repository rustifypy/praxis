"""What an uploaded file is: its columns, their types and their spread, read once on arrival.

`Profile` and not `Introspection` or `Catalog`: those are the warehouse probe's output and the
served model, and a file's shape is a third thing.

This exists so the first move after an upload is not a `run_python` to discover the columns. That
works, costs a round trip, and comes out differently every time — and a 200 MB CSV cannot be
handed to a model whole, so a profile is the only summary of one that is safe to put in a prompt.

The profile is a file in the workspace like everything else here. There is no table: the workspace
is the store, and `uploads/*` is already the record that a file arrived.
"""

import json
import shlex
from pathlib import Path

from pydantic import BaseModel

from . import container, scratch

PROFILER = Path(__file__).parent / "profiler.py"
TIMEOUT_S = 60.0
"""Half `EXEC_TIMEOUT_S`. This runs while someone is watching an upload finish, and a file too
big to summarise in a minute is one to say that about rather than to keep waiting on."""


class ColumnProfile(BaseModel):
    name: str
    type: str
    distinct: int = 0
    nulls: float = 0.0
    min: str | None = None
    max: str | None = None
    unique: bool = False


class Profile(BaseModel):
    path: str
    bytes: int = 0
    rows: int = 0
    columns: list[ColumnProfile] = []
    head: list[dict] = []
    note: str = ""
    """Why there is no shape here, or what had to be left out of one. Empty when the file read
    cleanly and whole."""

    def as_prompt(self) -> str:
        """What the agent is told it has. Compact on purpose — this is in the system prompt of
        every turn for the life of the chat, not just the one the file arrived in."""
        size = f"{self.bytes / 1_000_000:.1f} MB" if self.bytes >= 1_000_000 else f"{self.bytes} B"
        head = f"### {self.path} — {size}"
        if self.rows:
            head += f", {self.rows:,} rows × {len(self.columns)} columns"
        lines = [head]
        if self.note:
            lines.append(f"({self.note})")
        for column in self.columns:
            said = [f"{column.distinct:,} distinct"]
            if column.unique:
                said.append("unique")
            if column.nulls:
                said.append(f"{column.nulls:.0%} null")
            if column.min is not None and column.min != column.max:
                said.append(f"{column.min} … {column.max}")
            lines.append(f"- {column.name} ({column.type}) — {', '.join(said)}")
        for row in self.head:
            lines.append("  " + " | ".join(f"{v}" for v in row.values()))
        return "\n".join(lines)


async def of(chat_id: str, path: str) -> Profile:
    """Read one upload's shape in the sandbox, and keep the answer beside the file.

    Never raises: an upload whose profile failed is still an upload, and the reason belongs in
    `note` where the agent and the person who sent it can both read it.
    """
    target, refused = scratch.resolve(chat_id, path)
    if target is None or not target.is_file():
        return Profile(path=path, note=refused or f"{path} is not a file")
    profile = Profile(path=path, bytes=target.stat().st_size)

    up = await container.ensure_up()
    if not up.ok:
        profile.note = f"not profiled — the sandbox is not available: {up.message}"
        return _keep(chat_id, profile)

    # Copied in rather than baked into the image: the image is tagged by its Dockerfile's digest,
    # so a profiler shipped inside it would be served stale after every edit to this file.
    script = scratch.ensure(chat_id) / scratch.PROFILES / PROFILER.name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(PROFILER.read_bytes())

    where = f"{scratch.PROFILES}/{PROFILER.name}"
    result = await container.execute(
        chat_id, f"python {shlex.quote(where)} {shlex.quote(path)}", timeout=TIMEOUT_S
    )
    last = result.out.strip().splitlines()[-1] if result.out.strip() else ""
    try:
        # The script's own errors arrive *as* a note in that JSON. This is the case where it did
        # not get to print at all — killed at the timeout, or the container gone.
        profile = Profile(path=path, bytes=profile.bytes, **json.loads(last))
    except (ValueError, TypeError):
        profile.note = f"not profiled — exit {result.code}: {(result.err or result.out)[:200]}"
    return _keep(chat_id, profile)


def _keep(chat_id: str, profile: Profile) -> Profile:
    (scratch.ensure(chat_id) / scratch.PROFILES).mkdir(parents=True, exist_ok=True)
    _cache(chat_id, profile.path).write_text(profile.model_dump_json())
    return profile


def _cache(chat_id: str, path: str) -> Path:
    return scratch.ensure(chat_id) / scratch.PROFILES / f"{Path(path).name}.json"


def forget(chat_id: str, path: str) -> None:
    """Drop a file's cached shape, for when the file itself goes."""
    _cache(chat_id, path).unlink(missing_ok=True)


def saved(chat_id: str) -> list[Profile]:
    """Every upload this chat holds, profiled where one was read. The listing is the authority
    and the cache is only the shape: a file whose profile is missing or unreadable is still
    named, because the agent can open it either way."""
    out = []
    for path in scratch.uploaded(chat_id):
        cache = _cache(chat_id, path)
        try:
            out.append(Profile.model_validate_json(cache.read_text()))
        except (OSError, ValueError):
            out.append(Profile(path=path, note="not profiled"))
    return out
