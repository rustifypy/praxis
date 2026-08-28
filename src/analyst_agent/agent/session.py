import asyncio
from dataclasses import dataclass, field

from ..modelstore.catalog import Catalog
from ..modelstore.client import Bound


@dataclass
class QueryRecord:
    query: dict
    sql: str | None
    row_count: int
    duration_ms: int | None
    warnings: list[str]
    # The header this result was rendered with, so `set_chart` is checked against the columns
    # that exist rather than the ones the model remembers.
    columns: list[dict] = field(default_factory=list)
    # Where the rows themselves are, since this record holds only their shape.
    result_id: str = ""


@dataclass
class Session:
    catalog: Catalog
    client: Bound
    max_rows: int = 5_000
    executed: list[QueryRecord] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    chat_id: str = ""
    turn_id: str = ""
    cancel: asyncio.Event | None = None
    # Where a click on "run this" arrives. None means nothing is asked and every script runs.
    approvals: asyncio.Queue | None = None
    # The sandbox workspace these files live in, when it is not the chat's. A Phase sets it to
    # its Run's, which is how a Run's files stay out of every chat's workspace — and why it is
    # separate from `chat_id`: a Phase wants the workspace *without* the `results` rows, since a
    # Run keeps nothing in the database.
    workspace: str = ""
    # This turn's most recent successful result. Held because a Phase has no `results` row to
    # address by id, so this is the only copy of the rows `export_result` would write.
    rows: list[dict] = field(default_factory=list)

    @property
    def workspace_key(self) -> str:
        """Which sandbox workspace to read and write. `scratch` is the shared one tests get: a
        Session with neither is not a chat and not a Run, and must not be handed the workspaces
        root itself."""
        return self.workspace or self.chat_id or "scratch"


def replay(events: list[dict]) -> tuple[list[QueryRecord], list[dict]]:
    executed: list[QueryRecord] = []
    rejected: list[dict] = []
    for event in events:
        data = event["data"]
        if event["type"] != "tool_end" or data.get("tool") != "run_query":
            continue
        if data.get("ok"):
            executed.append(
                QueryRecord(
                    query=data.get("query", {}),
                    sql=data.get("sql"),
                    row_count=data.get("rows_total", 0),
                    duration_ms=data.get("duration_ms"),
                    warnings=data.get("warnings", []),
                    # Absent on events written before results had a header, in which case
                    # `set_chart` rejects for want of columns rather than raising.
                    columns=data.get("columns", []),
                    result_id=data.get("result_id", ""),
                )
            )
        else:
            rejected.append({"query": data.get("query", {}), "errors": data.get("errors", [])})
    return executed, rejected
