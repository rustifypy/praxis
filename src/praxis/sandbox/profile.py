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

    def as_prompt(self) -> str:
        # Compact on purpose: this sits in the system prompt for the life of the chat, not
        # only the turn the file arrived in.
        size = f"{self.bytes / 1_000_000:.1f} MB" if self.bytes >= 1_000_000 else f"{self.bytes} B"
        head = f"### {self.path} — {size}"
        if self.rows:
            head += f", {self.rows:,} rows × {len(self.columns)} columns"
        lines = [head]
        if self.note:
            lines.append(f"({self.note})")
        for column in self.columns:
            # Zero is "not counted": a profile the portal makes itself, while the sandbox is
            # down, has no distinct counts, and "0 distinct" would be a claim about the data.
            said = [f"{column.distinct:,} distinct"] if column.distinct else []
            if column.unique:
                said.append("unique")
            if column.nulls:
                said.append(f"{column.nulls:.0%} null")
            if column.min is not None and column.min != column.max:
                said.append(f"{column.min} … {column.max}")
            line = f"- {column.name} ({column.type})"
            lines.append(f"{line} — {', '.join(said)}" if said else line)
        for row in self.head:
            lines.append("  " + " | ".join(f"{value}" for value in row.values()))
        return "\n".join(lines)


async def saved(chat_id: str) -> list[Profile]:
    # An absent or unreachable sandbox yields nothing: a file's shape is worth having and not
    # worth failing a turn over.
    try:
        async with await open_sandbox() as sandbox:
            raw_profiles = await sandbox.profiles(chat_id)
    except SandboxError:
        return []
    profiles = []
    for raw in raw_profiles:
        try:
            profiles.append(Profile.model_validate(raw))
        except ValueError:
            profiles.append(Profile(path=str(raw.get("path", "?")), note="not profiled"))
    return profiles
