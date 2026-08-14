"""The sandbox tools: get a query's rows in, run a script over them.

Two tools and not a filesystem. The arithmetic this exists for — a ratio between two measures, a
period-over-period delta, a share of a total — is a dozen lines, so a script arrives whole and its
output comes back. It is saved to the workspace anyway, which is what lets the agent re-read what
it actually ran and leaves a computed figure's provenance on disk rather than only in a transcript.

`run_python` is the only thing that enters the container. `export_result` is host-side, because the
workspace is a bind mount — so an export works while the image is still building.
"""

import csv

from langchain.tools import tool

from ...core.events import Event, emit
from ...cube.shape import Column, export_value
from ...sandbox import container, scratch
from ..session import Session

EVENT_KEYS = frozenset({"tool", "label", "ok", "note", "errors", "exit_code", "output"})
"""Every key one of these events may carry — asserted by a test. Absent on purpose: the script
body, the exported rows, and the whole of a command's output. A transcript records that a script
ran and how it ended; the workspace holds the script and the data, `.logs/` holds what it said."""

OUTPUT_LIMIT = 4_000
"""What a script's output is trimmed to. One number for both the model and the event, unlike a
query result's two: the log file is where the rest is read from, so a bigger event would put a
second copy of it through the event log for nobody's benefit."""


def build_code_tools(session: Session) -> list:
    # A Session with no chat has no workspace of its own — only tests build one, and they get a
    # shared directory rather than the `workspaces` root itself.
    chat_id = session.chat_id or "scratch"
    scratch.ensure(chat_id)

    def _start(name: str, label: str) -> None:
        emit(Event(type="tool_start", data={"tool": name, "label": label}))

    def _end(name: str, ok: bool, **data) -> None:
        emit(Event(type="tool_end", data={"tool": name, "ok": ok, **data}))

    @tool
    async def export_result(filename: str = "result.csv") -> str:
        """Write the most recent query's rows into the workspace as a CSV, and return its path
        and columns.

        This is the only way data reaches the sandbox: it has no network and cannot query
        anything itself. Call it after `run_query` and before `run_python`.
        """
        _start("export_result", filename)
        if not session.executed:
            _end("export_result", False)
            return "No query has run yet, so there is nothing to export. Call run_query first."

        record = session.executed[-1]
        stored = None
        if session.store is not None and record.result_id:
            stored = await session.store.result(record.result_id)
        if stored is None:
            _end("export_result", False)
            return (
                "That result was not kept, so it cannot be exported. Run the query again and "
                "export immediately."
            )

        target, refused = scratch.resolve(chat_id, filename)
        if target is None:
            _end("export_result", False, errors=[refused])
            return refused

        columns = [Column(**c) for c in stored["columns"]]
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="") as handle:
            writer = csv.writer(handle)
            # Member ids as the header, not labels: a script refers to `sales.invoice_total`,
            # which is what the agent knows the column by. The download route writes labels
            # instead, because a spreadsheet is read by a person.
            writer.writerow([c.id for c in columns])
            for row in stored["rows"]:
                writer.writerow([export_value(row.get(c.id), c) for c in columns])

        listed = ", ".join(c.id for c in columns)
        _end("export_result", True, note=f"{filename}, {stored['row_count']} rows")
        return f"Wrote {filename}: {stored['row_count']} rows. Columns: {listed}"

    @tool
    async def run_python(script: str) -> str:
        """Run a Python script in the sandbox and return what it printed.

        pandas, polars, duckdb, pyarrow, numpy, scipy, matplotlib and openpyxl are available and
        **there is no network**, so nothing can be downloaded or installed. The working directory
        is the workspace, so a file `export_result` wrote is right beside the script. Every call
        is a fresh interpreter — only files carry over — so print whatever you need to see.
        """
        saved = scratch.save_script(chat_id, script)
        _start("run_python", saved)

        up = await container.ensure_up()
        if not up.ok:
            _end("run_python", False, errors=[up.message])
            return f"The sandbox is not available: {up.message}"

        result = await container.execute(chat_id, f"python {saved}")
        both = "\n".join(part for part in (result.out.rstrip(), result.err.rstrip()) if part)
        shown, log = scratch.record(chat_id, "run", both, OUTPUT_LIMIT)
        _end("run_python", result.ok, exit_code=result.code, output=shown)

        head = f"{saved} exited {result.code}"
        if result.code in (124, 137):
            head += f" — killed at the {container.EXEC_TIMEOUT_S:.0f}s limit"
        # The memory and process limits belong to the container, not to this script, so a script
        # that exceeded one takes the container with it. Said plainly, because the alternative is
        # the agent reading a dead-container error as a bug in its own code.
        if result.code and await container.state() != "running":
            head += (
                ". The sandbox itself was killed — most likely this script exceeded the memory "
                "limit. The workspace is intact and the next run starts a new one"
            )
        if log:
            head += f"; output trimmed, the whole log is at {log}"
        return f"{head}\n\n{shown}" if shown else f"{head}\n(no output)"

    return [export_result, run_python]
