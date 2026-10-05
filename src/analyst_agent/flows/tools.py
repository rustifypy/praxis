"""What an agent Phase can do: its **Bundles**, plus `write_output`.

A Phase names Bundles and never tools, so a tool can be renamed without breaking a file somebody
wrote. `BUNDLES` in `contract` is the closed menu; this is where each one turns into tools.

**A Phase's `query` Bundle is not the chat's.** A chat's `run_query` answers a person: rows in the
reply, a table and a chart in the transcript, capped at `max_rows`. A Phase answers the next
Phase, and what it hands over is a file. So a Phase gets `extract` in its place — the same gate,
tiers 1-3 through `gate.check` and tier 4 through `gate.pages` — which writes every row to a CSV
and gives the model the file's shape and nothing else. The rows never reach the model, so it has
nothing to re-type into `write_output`, and none reach the Run's log either. For the same reason
the `python` Bundle is `run_python` alone: `export_result` exists to move a chat's stored rows
into the sandbox, and an extract is already there.

`write_output` is in no Bundle. Every agent Phase has it, because producing the outputs it
declared is what being an agent Phase *means* — and because it is the only way a Phase with no
`python` Bundle can write a file at all. It writes to portal disk directly rather than through the
sandbox, so a Phase whose job is three paragraphs of prose needs no sandbox to be up.
"""

import contextlib
import csv
import json
import time
from dataclasses import asdict
from pathlib import Path

import httpx
from langchain.tools import tool

from ..agent.session import QueryRecord, Session
from ..agent.tools.code import build_code_tools
from ..agent.tools.query import build_query_tools
from ..agent.tools.trace import end, start
from ..core.config import settings
from ..modelstore import gate
from ..modelstore.client import StoreError
from ..modelstore.shape import columns_of, describe_query, export_value
from ..sandbox import SandboxError, open_sandbox
from ..sandbox.profile import ColumnProfile, Profile
from .contract import Phase
from .prompt import Material

EVENT_KEYS = frozenset(
    {
        "tool",
        "label",
        "ok",
        "note",
        "errors",
        "warnings",
        "query",
        "sql",
        "rows_total",
        "columns",
        "duration_ms",
    }
)
"""Every key an `extract` event may carry, asserted by a test. No `preview` and no `chart`: the
browser draws a result table from `preview`, and a Run's log is not where data is read."""

# What the model is shown of an extract when the sandbox cannot profile it. The sandbox's own
# profile says how many head rows it keeps; this is the portal's answer to the same question.
HEAD_ROWS = 5


def build_phase_tools(
    session: Session,
    phase: Phase,
    output: Path,
    scratch: Path,
    pushed: set[str],
    material: Material | None = None,
) -> list:
    """Every tool this Phase may call.

    `output` and `scratch` are the Phase's folders inside the Run — passed in already resolved,
    because deciding where a Phase's files go is the runner's job and re-deciding it here is how
    two answers to that question would appear. `pushed` collects the sandbox paths `extract`
    uploaded, so `_collect` does not copy back a file the portal already holds. `material` carries
    the Phase's prepared queries and scripts, read once by the runner with the rest of its inputs.
    """
    material = material or Material()
    queries, scripts = dict(material.queries), dict(material.scripts)
    # A prepared script runs without asking: it was read and saved into the Flow, the same review a
    # `kind: script` Phase gets. Keyed by its text, so an edited copy sent to `run_python` is a new
    # script and asks like one.
    session.prepared = {text: name for name, text in scripts.items()}
    # The declared outputs `extract` wrote. `write_output` refuses these: the extract is data that
    # came through Cube, and nothing a model types may replace it.
    extracted: set[str] = set()
    tools = [_write_output(phase, output, extracted, session)]
    if "query" in phase.spec.tools:
        chat_tools = {"search_model", "explain_last_query"}
        tools.extend(t for t in build_query_tools(session) if t.name in chat_tools)
        tools.extend(_extract(session, phase, output, scratch, pushed, extracted, queries))
    if "python" in phase.spec.tools:
        run_python = next(t for t in build_code_tools(session) if t.name == "run_python")
        tools.append(run_python)
        if scripts:
            tools.append(_run_prepared(run_python, scripts))
    return tools


def _run_prepared(run_python, scripts: dict[str, str]):
    @tool
    async def run_prepared(name: str) -> str:
        """Run one of this Phase's prepared scripts exactly as it was written, by its filename
        (for example forecast.py). It needs no approval. Prefer it to writing your own; to change
        it, send your edited version to `run_python` instead and say why in your output."""
        if name not in scripts:
            start("run_python", name)
            end("run_python", False, errors=[f"{name} is not a prepared script"])
            return f"{name} is not one of this Phase's prepared scripts: {', '.join(scripts)}."
        return await run_python.ainvoke({"script": scripts[name]})

    return run_prepared


def _extract(
    session: Session,
    phase: Phase,
    output: Path,
    scratch: Path,
    pushed: set[str],
    extracted: set[str],
    queries: dict[str, str],
) -> list:
    declared = set(phase.spec.outputs)
    can_run_code = "python" in phase.spec.tools

    @tool
    async def extract(query: dict, filename: str) -> str:
        """Run a Cube JSON query and write every row it returns to a CSV file. Returns the file's
        shape — columns, row count, a few rows — and never the rows themselves.

        Keys: measures, dimensions, timeDimensions, filters, segments, order, limit. Leave
        `limit` out to get every row; set it only for a top-N. `filename` is a plain name ending
        in .csv — use the name phase.yml declares when the file is this Phase's output. The file
        is in the working directory for `run_python`.
        """
        return await pull(query, filename, describe_query(query, session.catalog))

    @tool
    async def extract_prepared(name: str) -> str:
        """Run one of this Phase's prepared queries exactly as it was written, by its name in
        queries/ (for example stock_position.json). It writes stock_position.csv and returns the
        file's shape. Prefer it to writing your own query with `extract`."""
        if name not in queries:
            start("extract", name)
            end("extract", False, errors=[f"{name} is not a prepared query"])
            return f"{name} is not one of this Phase's prepared queries: {', '.join(queries)}."
        return await pull(json.loads(queries[name]), Path(name).stem + ".csv", f"prepared {name}")

    async def pull(query: dict, filename: str, label: str) -> str:
        start("extract", label, query=query)
        if (
            "/" in filename
            or "\\" in filename
            or filename.startswith(".")
            or not (filename.endswith(".csv"))
        ):
            end("extract", False, errors=[f"{filename!r} is not a plain .csv filename"])
            return f"{filename!r} is not a plain filename ending in .csv. Use one like orders.csv."

        # Read before `check`, which overwrites `limit` with its own page-sized cap. An absent
        # limit is "every row", and one past the ceiling is how the ceiling is noticed.
        asked = query.get("limit")
        most = asked if isinstance(asked, int) and asked > 0 else settings.flow_max_rows + 1
        checked = await gate.check(query, session.catalog, session.client, settings.flow_page_rows)
        if not checked.ok:
            end("extract", False, query=query, errors=checked.errors)
            return checked.as_message() + "\n\nFix the query and call extract again."

        columns = columns_of(query, session.catalog)
        target = (output if filename in declared else scratch) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        head: list[dict] = []
        began = time.monotonic()
        try:
            # Written a page at a time and never held whole: memory is one page, whatever the
            # result's size. Member ids as the header, because a script refers to them.
            with target.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([column.id for column in columns])
                async for rows in gate.pages(
                    checked, session.client, settings.flow_page_rows, most
                ):
                    for row in rows:
                        values = [export_value(row.get(column.id), column) for column in columns]
                        writer.writerow(values)
                        if len(head) < HEAD_ROWS:
                            head.append(dict(zip([c.id for c in columns], values, strict=True)))
                    total += len(rows)
                    if session.cancel is not None and session.cancel.is_set():
                        raise _Stopped
            # A file that stopped at the ceiling looks finished and is not, so it is not kept.
            # The next Phase would read it as the whole result.
            if total > settings.flow_max_rows:
                target.unlink(missing_ok=True)
                cap = f"{settings.flow_max_rows:,}"
                end("extract", False, query=query, errors=[f"more than {cap} rows"])
                return (
                    f"This query returns more than {cap} rows, so nothing was written. Narrow it "
                    "with filters, or extract it in parts — one file per time window."
                )
        except _Stopped:
            target.unlink(missing_ok=True)
            end("extract", False, note="stopped by the user")
            return "The user stopped this Phase. Nothing was written. Run nothing else."
        except (StoreError, httpx.RequestError, OSError) as exc:
            target.unlink(missing_ok=True)
            end("extract", False, query=query, errors=[f"{type(exc).__name__}: {exc}"])
            return f"The extract failed after {total:,} rows, so nothing was written: {exc}"
        duration_ms = int((time.monotonic() - began) * 1000)
        if filename in declared:
            extracted.add(filename)

        shape = await _hand_over(session.workspace_key, target, pushed)
        if shape is None:
            shape = Profile(
                path=filename,
                bytes=target.stat().st_size,
                rows=total,
                columns=[ColumnProfile(name=c.id, type=c.type or c.kind) for c in columns],
                head=head,
                note="the sandbox did not answer, so this is the portal's count and first rows",
            )

        header = [asdict(column) for column in columns]
        session.executed.append(
            QueryRecord(
                query=query,
                sql=checked.sql,
                row_count=total,
                duration_ms=duration_ms,
                warnings=checked.warnings,
                columns=header,
            )
        )
        end(
            "extract",
            True,
            query=query,
            sql=checked.sql,
            rows_total=total,
            columns=header,
            note=f"{filename} · {total:,} rows",
            duration_ms=duration_ms,
            warnings=checked.warnings,
        )

        where = "output" if filename in declared else "scratch (not a declared output)"
        lines = [f"Wrote {filename} to {where}: {total:,} rows."]
        if checked.warnings:
            lines.append(checked.as_message())
        if not total:
            lines.append("No data matched. Check filters before concluding the answer is zero.")
        lines.append(shape.as_prompt())
        lines.append(
            "That is the file's shape, not its contents. Read the file with `run_python` to "
            "compute from it, and print aggregates rather than rows."
            if can_run_code
            else "That is the file's shape, not its contents, and this Phase cannot run code. "
            "Work from the columns and the rows shown."
        )
        return "\n\n".join(lines)

    return [extract, extract_prepared] if queries else [extract]


class _Stopped(Exception):
    """The Run was stopped between two pages."""


async def _hand_over(workspace: str, target: Path, pushed: set[str]) -> Profile | None:
    """Put the extract in the Run's sandbox workspace and return the sandbox's profile of it, or
    `None` if there is no sandbox to ask.

    The same-named file is deleted first, for the reason `runner._gather` does it: the sandbox
    renames on collision, so a second extract of `orders.csv` would land as `orders-2.csv` and a
    script asking for the name it was told would read the first one.
    """
    try:
        async with await open_sandbox() as sandbox:
            found, _ = await sandbox.listing(workspace)
            for path in found:
                if path.rsplit("/", 1)[-1] == target.name:
                    with contextlib.suppress(SandboxError):
                        await sandbox.delete(workspace, path)
            with target.open("rb") as handle:
                answer = await sandbox.upload(workspace, target.name, handle)
    except (SandboxError, OSError):
        return None
    path = str(answer.get("path") or target.name)
    pushed.add(path)
    try:
        return Profile.model_validate(answer.get("profile") or {"path": path})
    except ValueError:
        return None


def _write_output(phase: Phase, output: Path, extracted: set[str], session: Session):
    declared = list(phase.spec.outputs)

    @tool
    async def write_output(filename: str, text: str) -> str:
        """Write one of this Phase's declared outputs. `filename` must be exactly as declared.

        This is how a Phase produces its result. The file lands in the Phase's output folder,
        where the person reviewing this Run reads it and where the next Phase looks for it.
        """
        start("write_output", filename)
        # Refused rather than accepted-and-put-somewhere, because the declaration is the contract
        # the next Phase's inputs were written against. A near-miss filename would leave the Phase
        # looking successful and the Phase after it reading nothing.
        if filename not in declared:
            end("write_output", False, errors=[f"{filename} is not declared by this Phase"])
            listed = ", ".join(declared) or "(this Phase declares no outputs)"
            return (
                f"{filename} is not one of this Phase's declared outputs. Write one of: {listed}. "
                "If a different file is genuinely needed, the Phase's phase.yml has to declare it."
            )
        if filename in extracted:
            end("write_output", False, errors=[f"{filename} was written by extract"])
            return (
                f"{filename} was written by extract and is already this Phase's output. Do not "
                "call write_output for it."
            )
        # Seen live: a prepared script wrote demand_forecast.csv, and the agent then printed the
        # file and re-typed all of it here. The collected copy won, so the artifact was right —
        # but the model had read every row, which a Phase must never do.
        if filename in {path.rsplit("/", 1)[-1] for path in session.wrote}:
            end("write_output", False, errors=[f"{filename} was written by a script"])
            return (
                f"{filename} was written by a script in this Phase and is already its output — "
                "it is collected for you. Do not call write_output for it, and do not print it."
            )
        # An empty declared output is a Phase that produced nothing while looking like it
        # succeeded. Seen live: told not to re-type a CSV that a tool had already made, the model
        # called this with an empty string rather than not calling it — and only the sandbox's
        # copy overwriting it kept the artifact correct. Refused here so that rescue is not what
        # the design rests on.
        if not text.strip():
            end("write_output", False, errors=[f"{filename} would be empty"])
            return (
                f"{filename} would be empty, so nothing was written. If a tool has already "
                "produced this file, it is collected for you — do not call write_output for it "
                "at all. Otherwise write the content."
            )
        try:
            output.mkdir(parents=True, exist_ok=True)
            (output / filename).write_text(text)
        except OSError as exc:
            end("write_output", False, errors=[str(exc)])
            return f"{filename} could not be written: {exc}"
        end("write_output", True, note=f"{filename}, {len(text)} characters")
        return f"Wrote {filename} ({len(text)} characters)."

    return write_output
