import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from langchain.tools import tool

from ..chat.events import Event, emit
from ..cube import gate
from ..cube.catalog import Catalog
from ..cube.client import CubeClient
from ..cube.describe import describe_query
from ..cube.shape import Column, columns_of
from ..cube.visualize import MAX_POINTS, chart_for, validate_spec
from ..grounding import Grounding
from ..retrieval import LexicalRetriever, render

if TYPE_CHECKING:
    # Only for the annotation: chat.store imports QueryRecord from this module, so a real
    # import here would be a cycle.
    from ..chat.store import ChatStore


@dataclass
class QueryRecord:
    query: dict
    sql: str | None
    row_count: int
    duration_ms: int | None
    warnings: list[str]
    columns: list[dict] = field(default_factory=list)
    """The header this result was rendered with. Kept so `set_chart` can be checked against
    the columns that exist rather than the ones the model remembers — and rebuilt by
    `replay_session`, so the check still works on turn three and after a restart."""


@dataclass
class Session:
    """Per-request state. Holds provenance so answers can be audited."""

    catalog: Catalog
    client: CubeClient
    grounding: Grounding
    max_rows: int = 5_000
    executed: list[QueryRecord] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    store: "ChatStore | None" = None
    chat_id: str = ""
    turn_id: str = ""
    """Where a result set is kept, and under which chat and turn.

    Absent on the `/ask` path, which has no chat to hang a result on: nothing is persisted
    and no `result_id` is emitted, so a caller with no browser behaves exactly as before.
    """


def build_tools(session: Session, preview_rows: int = 30, event_rows: int = MAX_POINTS) -> list:
    retriever = LexicalRetriever(session.catalog)

    @tool
    def search_model(question: str) -> str:
        """Find the measures and dimensions available for a question. Returns member
        ids, types, notes, and the valid filter operators for each. Call this before
        building any query."""
        emit(Event(type="tool_start", data={"tool": "search_model", "label": question}))
        hits = retriever.search(question)
        emit(
            Event(
                type="tool_end",
                data={"tool": "search_model", "ok": True, "hits": len(hits)},
            )
        )
        if not hits:
            return "The semantic model is empty."
        certified = session.grounding.render_certified(question)
        out = render(hits)
        if certified:
            out += f"\n\n# Certified queries for similar requests\n\n{certified}"
        return out

    @tool
    async def run_query(query: dict) -> str:
        """Execute a Cube JSON query. Keys: measures, dimensions, timeDimensions,
        filters, segments, order, limit, offset. Returns rows, or a rejection
        explaining what to fix."""
        emit(
            Event(
                type="tool_start",
                data={
                    "tool": "run_query",
                    "label": describe_query(query, session.catalog),
                    "query": query,
                },
            )
        )
        result = await gate.run(query, session.catalog, session.client, session.max_rows)

        if not result.ok:
            session.rejected.append({"query": query, "errors": result.errors})
            # A rejection is a normal step in the trace, not an error event — the agent
            # reads the reason and retries, and the user should see that it happened.
            emit(
                Event(
                    type="tool_end",
                    data={
                        "tool": "run_query",
                        "ok": False,
                        "query": query,
                        "errors": result.errors,
                    },
                )
            )
            return result.as_message() + "\n\nFix the query and call run_query again."

        rows = result.rows or []
        columns = [asdict(c) for c in columns_of(query, session.catalog)]
        picked = chart_for(query, session.catalog, rows)
        chart = asdict(picked) if picked else None
        # The handle the browser fetches the whole result by. The event carries only a
        # head of the rows — enough to render a table the moment a transcript replays,
        # without putting a 5000-row payload through the event log on every reload.
        result_id = ""
        if session.store is not None and session.chat_id:
            result_id = await session.store.save_result(
                session.chat_id, session.turn_id, columns, rows
            )
        emit(
            Event(
                type="tool_end",
                data={
                    "tool": "run_query",
                    "ok": True,
                    # Repeated from tool_start so a turn can be rebuilt from tool_end
                    # alone — see ChatStore.replay_session.
                    "query": query,
                    "sql": result.sql,
                    "rows_total": len(rows),
                    "columns": columns,
                    "result_id": result_id,
                    # Derived, not asked for: the chart is free, instant on a replayed
                    # transcript, and cannot plot a column nobody fetched. `None` is a
                    # normal outcome — see visualize.chart_for.
                    "chart": chart,
                    # Two different limits on purpose: the model pays per token and reads
                    # `preview_rows`, the browser pays per event row and renders
                    # `event_rows`. They were one number, which meant making the table
                    # feel complete would have cost the model 70 rows of context. The
                    # browser's limit is `visualize.MAX_POINTS`, which is what lets a chart
                    # be drawn from the event alone rather than fetched.
                    "preview": rows[:event_rows],
                    "duration_ms": result.duration_ms,
                    "warnings": result.warnings,
                },
            )
        )
        session.executed.append(
            QueryRecord(
                query=query,
                sql=result.sql,
                row_count=len(rows),
                duration_ms=result.duration_ms,
                warnings=result.warnings,
                columns=columns,
            )
        )
        head = f"{len(rows)} rows in {result.duration_ms}ms."
        if result.warnings:
            head += "\n" + result.as_message()
        if not rows:
            return head + "\nNo data matched. Check filters before concluding the answer is zero."
        shown = rows[:preview_rows]
        body = json.dumps(shown, indent=2, default=str)
        if len(rows) > preview_rows:
            body += f"\n... {len(rows) - preview_rows} more rows not shown"
        return f"{head}\n\n{body}"

    @tool
    def load_grounding(name: str) -> str:
        """Load the full instructions for a named skill or agent_requested rule."""
        emit(Event(type="tool_start", data={"tool": "load_grounding", "label": name}))
        pool = session.grounding.skills + session.grounding.rules
        found = next((a for a in pool if a.name == name), None)
        emit(
            Event(
                type="tool_end",
                data={"tool": "load_grounding", "ok": found is not None},
            )
        )
        if not found:
            return f"No artefact named '{name}'. Available: {[a.name for a in pool]}"
        return found.body

    @tool
    def set_chart(spec: dict) -> str:
        """Replace the chart drawn for the most recent result. Rarely needed — one is
        already chosen from the query's shape. Keys: type (line, bar or kpi), x (a
        dimension or time column id), y (a list of measure column ids), series (optional,
        line only). Use the column ids exactly as run_query reported them."""
        emit(
            Event(
                type="tool_start",
                data={"tool": "set_chart", "label": f"Charting as {spec.get('type', '?')}"},
            )
        )
        if not session.executed:
            emit(Event(type="tool_end", data={"tool": "set_chart", "ok": False}))
            return "No query has run yet, so there is nothing to chart."

        columns = [Column(**c) for c in session.executed[-1].columns]
        checked, errors = validate_spec(spec, columns)
        if checked is None:
            # A rejection is text the agent can act on, exactly as the query gate does.
            emit(Event(type="tool_end", data={"tool": "set_chart", "ok": False, "errors": errors}))
            listed = ", ".join(f"{c.id} ({c.kind})" for c in columns)
            return (
                "Chart rejected:\n"
                + "\n".join(f"- {e}" for e in errors)
                + f"\n\nColumns in this result: {listed}"
            )

        emit(
            Event(
                type="tool_end",
                data={"tool": "set_chart", "ok": True, "chart": asdict(checked)},
            )
        )
        return f"The result is now drawn as a {checked.type} chart."

    @tool
    def explain_last_query() -> str:
        """Show the SQL Cube generated for the most recent successful query."""
        emit(
            Event(
                type="tool_start",
                data={"tool": "explain_last_query", "label": "Reading the last query"},
            )
        )
        emit(
            Event(
                type="tool_end",
                data={"tool": "explain_last_query", "ok": bool(session.executed)},
            )
        )
        if not session.executed:
            return "No query has run yet."
        last = session.executed[-1]
        return f"```json\n{json.dumps(last.query, indent=2)}\n```\n\n```sql\n{last.sql}\n```"

    return [search_model, run_query, load_grounding, set_chart, explain_last_query]
