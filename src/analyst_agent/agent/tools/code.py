import asyncio
import csv
import io
import time

from langchain.tools import tool

from ...core import transcript
from ...core.events import Event, emit
from ...modelstore.shape import Column, export_value
from ...sandbox import SandboxError, open_sandbox
from ..session import Session
from .trace import end, start

EVENT_KEYS = frozenset(
    {"tool", "label", "ok", "note", "errors", "exit_code", "output", "wrote", "delta", "script"}
)
"""Every key one of these events may carry, asserted by a test. `wrote` and `script` are paths
and not contents: the file is in the sandbox's workspace and the download route reads it. Only
the `approval` event carries a script's text, and it is never stored."""

# How many of a run's files are named: the workspace holds them all either way.
WROTE_LIMIT = 10

# How often a running script's output is pushed, and how much of it is pushed at all. Past the
# limit the live view stops; `tool_end` and the log file carry the whole of it regardless.
STREAM_INTERVAL_S = 0.1
STREAM_LIMIT = 20_000

# What one turn may spend in the sandbox, since the service only caps a single call. Provisional:
# no long run has been measured yet.
TURN_SECONDS = 300.0
TURN_OUTPUT = 20_000


def build_code_tools(session: Session) -> list:
    # Which sandbox workspace these files live in: the chat's, or a Run's when a Phase set one.
    # `Session.workspace_key` is the one place that decides, so a Phase cannot end up writing
    # into a chat's directory by having asked the question a different way.
    workspace = session.workspace_key
    # Per turn: a fresh agent, and so a fresh closure, is built for each one.
    spent_seconds = 0.0
    spent_chars = 0
    approving = session.approvals is not None

    @tool
    async def export_result(filename: str = "result.csv") -> str:
        """Write the most recent query's rows into the workspace as a CSV, and return its path
        and columns.

        This is the only way a query's rows reach the sandbox: it holds no credential and cannot
        sign a token, so it cannot query anything itself. Call it after `run_query` and before
        `run_python`.
        """
        try:
            start("export_result", filename)
            if not session.executed:
                end("export_result", False)
                return "No query has run yet, so there is nothing to export. Call run_query first."

            record = session.executed[-1]
            stored = await transcript.result(record.result_id) if record.result_id else None
            # The stored copy first, because it outlives the turn: "export what you fetched
            # earlier" has to keep working on turn three, and only a `results` row can answer
            # that. A Phase has no such row — a Run keeps nothing in the database — so it falls
            # back to the rows still in hand from its own query.
            raw_columns = stored["columns"] if stored else record.columns
            raw_rows = stored["rows"] if stored else session.rows
            if not raw_columns or (stored is None and not session.rows):
                end("export_result", False)
                return (
                    "That result was not kept, so it cannot be exported. Run the query again and "
                    "export immediately."
                )

            columns = [Column(**raw) for raw in raw_columns]
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            # Member ids as the header and not labels: a script refers to `sales.invoice_total`.
            # The download route writes labels instead, because a person reads a spreadsheet.
            writer.writerow([column.id for column in columns])
            for row in raw_rows:
                writer.writerow([export_value(row.get(column.id), column) for column in columns])

            # Handed over rather than written directly: the workspace is the sandbox's, and the
            # path it lands on is its decision.
            async with await open_sandbox() as sandbox:
                landed = await sandbox.upload(workspace, filename, buffer.getvalue().encode())
                path = landed["path"]

            listed = ", ".join(column.id for column in columns)
            end("export_result", True, note=f"{path}, {len(raw_rows)} rows")
            return f"Wrote {path}: {len(raw_rows)} rows. Columns: {listed}"
        except SandboxError as exc:
            end("export_result", False, errors=[str(exc)])
            return f"The sandbox is not available, so nothing was exported: {exc}"

    @tool
    async def run_python(script: str) -> str:
        """Run a Python script in the sandbox and return what it printed.

        pandas, polars, duckdb, pyarrow, numpy, scipy, matplotlib and openpyxl are available.
        Nothing can be installed, so work with those. The working directory is the workspace, so
        a file `export_result` wrote — or one the user uploaded to `uploads/` — is right beside
        the script. Every call is a fresh interpreter — only files
        carry over — so print whatever you need to see.

        Any file the script writes is shown to the user and offered as a download, images
        included, so a chart you save is a chart they can see. Give it a name that says what it
        is.
        """
        nonlocal spent_seconds, spent_chars, approving
        try:
            if session.cancel is not None and session.cancel.is_set():
                return "The user stopped this turn. Run nothing else."
            if spent_seconds >= TURN_SECONDS or spent_chars >= TURN_OUTPUT:
                spent = f"{spent_seconds:.0f}s and {spent_chars} characters"
                start("run_python", "over budget")
                end("run_python", False, errors=[f"this turn has spent {spent} in the sandbox"])
                return (
                    f"This turn's sandbox budget is spent ({spent}). Answer from what you have "
                    "already computed."
                )

            start("run_python", "a script")

            # Shown before it is saved: the sandbox saves it as it runs, so the text shown here is
            # the text sent, and `tool_end` names the file it landed in.
            if approving:
                emit(
                    Event(
                        type="approval",
                        data={"tool": "run_python", "label": "run this script", "script": script},
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
                    end("run_python", False, note="declined by the user")
                    return (
                        "The user declined to run this script. Ask what they would like "
                        "changed, or answer without it."
                    )

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

            started = time.monotonic()
            async with await open_sandbox(timeout=None) as sandbox:
                runner = asyncio.create_task(sandbox.run(workspace, script, on_output=on_output))
                stop = asyncio.create_task((session.cancel or asyncio.Event()).wait())
                done, _ = await asyncio.wait({runner, stop}, return_when=asyncio.FIRST_COMPLETED)
                stop.cancel()

                stopped = runner not in done
                if stopped:
                    # Asked of the service rather than done to the request: cancelling the stream
                    # would leave the script running with nobody reading it.
                    await sandbox.kill(workspace)
                result = await runner

            spent_seconds += time.monotonic() - started
            shown = "\n".join(part for part in (result.out.rstrip(), result.err.rstrip()) if part)
            spent_chars += len(shown)
            end(
                "run_python",
                result.ok,
                exit_code=result.code,
                output=shown,
                script=result.script,
                wrote=result.wrote[:WROTE_LIMIT],
                note="stopped by the user" if stopped else "",
            )

            head = f"{result.script} exited {result.code}"
            if result.wrote:
                named = ", ".join(result.wrote[:WROTE_LIMIT])
                more = len(result.wrote) - WROTE_LIMIT
                head += f"; wrote {named}" + (f" and {more} more" if more > 0 else "")
            if stopped:
                head += " — stopped by the user"
            elif result.expired:
                head += " — killed at the sandbox's time limit"
            for log in (result.out_log, result.err_log):
                if log:
                    head += f"; the whole output is at {log}"
            return f"{head}\n\n{shown}" if shown else f"{head}\n(no output)"
        except SandboxError as exc:
            end("run_python", False, errors=[str(exc)])
            return f"The script could not be run: {exc}"

    return [export_result, run_python]
