"""The sandbox tools: get a query's rows in, run a script over them.

Two tools and not a filesystem. The arithmetic this exists for — a ratio between two measures, a
period-over-period delta, a share of a total — is a dozen lines, so a script arrives whole and its
output comes back. It is saved to the workspace anyway, which is what lets the agent re-read what
it actually ran and leaves a computed figure's provenance on disk rather than only in a transcript.

`run_python` is the only thing that enters the container. `export_result` is host-side, because the
workspace is a bind mount — so an export works while the image is still building.
"""

import asyncio
import csv
import time

from langchain.tools import tool

from ...core.events import Event, emit
from ...cube.shape import Column, export_value
from ...sandbox import container, scratch
from ..session import Session

EVENT_KEYS = frozenset(
    {"tool", "label", "ok", "note", "errors", "exit_code", "output", "wrote", "delta", "script"}
)
"""Every key one of these events may carry — asserted by a test. Absent on purpose: the exported
rows, and the whole of a command's output. A transcript records that a script ran and how it
ended; the workspace holds the script and the data, `.logs/` holds what it said. `wrote` is paths
and not contents, for the same reason: the file is on disk in a bind mount, and the download route
is how it is read. `script` is the exception and is carried by the `approval` event alone — which
is never stored, and is asking about a file already on disk."""

OUTPUT_LIMIT = 4_000
"""What a script's output is trimmed to. One number for both the model and the event, unlike a
query result's two: the log file is where the rest is read from, so a bigger event would put a
second copy of it through the event log for nobody's benefit."""

WROTE_LIMIT = 10
"""How many of a run's files are named. A script that writes three hundred PNGs has said what it
did by the tenth, and the event is the transcript's copy — the workspace holds them all."""

STREAM_INTERVAL_S = 0.1
STREAM_LIMIT = 20_000
"""How often a running script's output is pushed, and how much of it is pushed at all. Past the
limit the live view stops: `tool_end` and the log file carry the whole of it either way."""

TURN_SECONDS = 300.0
TURN_OUTPUT = 20_000
"""What one turn may spend in the sandbox. `EXEC_TIMEOUT_S` and `OUTPUT_LIMIT` cap a single call,
and forty of those is eighty minutes. Provisional: no long run has been measured yet."""


def build_code_tools(session: Session) -> list:
    # A Session with no chat has no workspace of its own — only tests build one, and they get a
    # shared directory rather than the `workspaces` root itself.
    chat_id = session.chat_id or "scratch"
    scratch.ensure(chat_id)
    # Per turn: a fresh agent, and so a fresh closure, is built for each one.
    spent_seconds = 0.0
    spent_chars = 0
    approving = session.approvals is not None

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
        is the workspace, so a file `export_result` wrote — or one the user uploaded to
        `uploads/` — is right beside the script. Every call is a fresh interpreter — only files
        carry over — so print whatever you need to see.

        Any file the script writes is shown to the user and offered as a download, images
        included, so a chart you save is a chart they can see. Give it a name that says what it
        is.
        """
        nonlocal spent_seconds, spent_chars, approving
        if session.cancel is not None and session.cancel.is_set():
            return "The user stopped this turn. Run nothing else."
        if spent_seconds >= TURN_SECONDS or spent_chars >= TURN_OUTPUT:
            spent = f"{spent_seconds:.0f}s and {spent_chars} characters"
            _start("run_python", "over budget")
            _end("run_python", False, errors=[f"this turn has spent {spent} in the sandbox"])
            return (
                f"This turn's sandbox budget is spent ({spent}). Answer from what you have "
                "already computed."
            )

        saved = scratch.save_script(chat_id, script)
        _start("run_python", saved)

        # Saved before it is offered, so what the page shows and what would run are one file.
        if approving:
            emit(
                Event(
                    type="approval",
                    data={"tool": "run_python", "label": saved, "script": script},
                )
            )
            waiting = asyncio.create_task(session.approvals.get())
            stop = asyncio.create_task((session.cancel or asyncio.Event()).wait())
            done, _ = await asyncio.wait({waiting, stop}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            # Stopping the turn is the third answer to the question, and it means no.
            choice = waiting.result() if waiting in done else "no"
            waiting.cancel()
            if choice == "always":
                approving = False
            if choice == "no":
                _end("run_python", False, note="declined by the user")
                return (
                    "The user declined to run this script. Ask what they would like changed, or "
                    "answer without it."
                )

        up = await container.ensure_up()
        if not up.ok:
            _end("run_python", False, errors=[up.message])
            return f"The sandbox is not available: {up.message}"

        pending = ""
        streamed = 0
        pushed = 0.0

        def on_output(chunk: str) -> None:
            nonlocal pending, streamed, pushed
            if streamed >= STREAM_LIMIT:
                return
            pending += chunk
            if (now := time.monotonic()) - pushed < STREAM_INTERVAL_S:
                return
            pushed, streamed = now, streamed + len(pending)
            emit(Event(type="output", data={"tool": "run_python", "delta": pending}))
            pending = ""

        before = scratch.snapshot(chat_id)
        command = f"echo $$ > {scratch.pid_file(saved)}; exec python {saved}"
        started = time.monotonic()
        runner = asyncio.create_task(container.execute(chat_id, command, on_output=on_output))
        stop = asyncio.create_task((session.cancel or asyncio.Event()).wait())
        done, _ = await asyncio.wait({runner, stop}, return_when=asyncio.FIRST_COMPLETED)
        stop.cancel()

        stopped = runner not in done
        if stopped:
            # The pid file may not be there yet: an exec takes ~65ms to start, and stop can be
            # pressed inside that window.
            for _ in range(40):
                if pid := scratch.pid(chat_id, saved):
                    await container.kill(pid)
                    break
                await asyncio.sleep(0.05)
        result = await runner
        spent_seconds += time.monotonic() - started

        wrote = [p for p, mark in scratch.snapshot(chat_id).items() if before.get(p) != mark]

        both = "\n".join(part for part in (result.out.rstrip(), result.err.rstrip()) if part)
        shown, log = scratch.record(chat_id, "run", both, OUTPUT_LIMIT)
        spent_chars += len(shown)
        _end(
            "run_python",
            result.ok,
            exit_code=result.code,
            output=shown,
            wrote=wrote[:WROTE_LIMIT],
            note="stopped by the user" if stopped else "",
        )

        head = f"{saved} exited {result.code}"
        if wrote:
            named = ", ".join(wrote[:WROTE_LIMIT])
            more = len(wrote) - WROTE_LIMIT
            head += f"; wrote {named}" + (f" and {more} more" if more > 0 else "")
        if stopped:
            head += " — stopped by the user"
        elif result.code in (124, 137):
            head += f" — killed at the {container.EXEC_TIMEOUT_S:.0f}s limit"

        if result.code and await container.state() != "running":
            head += (
                ". The sandbox itself was killed — most likely this script exceeded the memory "
                "limit. The workspace is intact and the next run starts a new one"
            )
        if log:
            head += f"; output trimmed, the whole log is at {log}"
        return f"{head}\n\n{shown}" if shown else f"{head}\n(no output)"

    return [export_result, run_python]
