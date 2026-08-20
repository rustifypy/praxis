"""What one chat turn knows: the catalog it is querying, the grounding it believes, and what
it has already run.

Provenance is agent vocabulary and storage is not, which is why `replay` lives here and takes
the events the store hands over. `transcript` names neither of these types.
"""

import asyncio
from dataclasses import dataclass, field

from ..cube.catalog import Catalog
from ..cube.client import CubeClient
from ..datamodel.grounding import Grounding
from ..transcript import ChatStore


@dataclass
class QueryRecord:
    query: dict
    sql: str | None
    row_count: int
    duration_ms: int | None
    warnings: list[str]
    columns: list[dict] = field(default_factory=list)
    """The header this result was rendered with, so `set_chart` is checked against the columns
    that exist rather than the ones the model remembers. Rebuilt by `replay`.
    """
    result_id: str = ""
    """Where the rows themselves are, since this record holds only their shape. `export_result`
    reads it to write them into the sandbox — and because `replay` recovers it, "export the
    numbers from that query" still works on turn three and after a restart."""


@dataclass
class Session:
    """Per-request state. Holds provenance so answers can be audited."""

    catalog: Catalog
    client: CubeClient
    grounding: Grounding
    max_rows: int = 5_000
    executed: list[QueryRecord] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    store: ChatStore | None = None
    chat_id: str = ""
    turn_id: str = ""
    """Where a result set is kept, and under which chat and turn."""
    cancel: asyncio.Event | None = None
    approvals: asyncio.Queue | None = None
    """Where a click on "run this" arrives. None means nothing is asked and every script runs."""


def replay(events: list[dict]) -> tuple[list[QueryRecord], list[dict]]:
    """Rebuild a Session's provenance from a transcript, so `explain_last_query` still answers
    on turn three and after a restart.
    """
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
                    # Absent on events written before results had a header; `set_chart`
                    # then rejects for want of columns rather than raising.
                    columns=data.get("columns", []),
                    result_id=data.get("result_id", ""),
                )
            )
        else:
            rejected.append({"query": data.get("query", {}), "errors": data.get("errors", [])})
    return executed, rejected
