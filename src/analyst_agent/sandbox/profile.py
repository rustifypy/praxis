"""What an uploaded file is: its columns, their types and their spread, read once on arrival.

`Profile` and not `Introspection` or `Catalog`: those are the warehouse probe's output and the
served model, and a file's shape is a third thing.

This exists so the first move after an upload is not a `run_python` to discover the columns. That
works, costs a round trip, and comes out differently every time — and a 200 MB CSV cannot be
handed to a model whole, so a profile is the only summary of one that is safe to put in a prompt.

The profile is a file in the sandbox's workspace like everything else there. There is no table:
the workspace is the store, and `uploads/*` is already the record that a file arrived. The
service reads a file's shape on arrival and keeps the answer beside it, so this side only ever
asks for what is already known.
"""

from pydantic import BaseModel

from .client import SandboxError, open_sandbox


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


async def saved(chat_id: str) -> list[Profile]:
    """Every upload this chat holds, profiled where it was read.

    One request per turn rather than one per file: this is in the system prompt for the life of
    the chat. A sandbox that is absent or not answering yields nothing — a file's shape is worth
    having and not worth failing a turn over.
    """
    try:
        async with await open_sandbox() as sandbox:
            body = await sandbox._send("GET", f"/profiles/{chat_id}")
    except SandboxError:
        return []
    out = []
    for raw in body.get("profiles", []):
        try:
            out.append(Profile.model_validate(raw))
        except ValueError:
            out.append(Profile(path=str(raw.get("path", "?")), note="not profiled"))
    return out
