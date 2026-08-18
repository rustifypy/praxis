import json
from dataclasses import asdict

from langchain.tools import tool

from ...core.events import Event, emit
from ...cube import gate
from ...cube.describe import describe_query
from ...cube.shape import Column, columns_of
from ...cube.visualize import MAX_POINTS, chart_for, validate_spec
from ..retrieval import LexicalRetriever, render
from ..session import QueryRecord, Session


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
        # `asdict` for the columns, which are dataclasses; `model_dump` for the chart, which
        # is a pydantic model because `set_chart` is handed one by the agent.
        columns = [asdict(c) for c in columns_of(query, session.catalog)]
        picked = chart_for(query, session.catalog, rows)
        chart = picked.model_dump() if picked else None
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
                    # alone — see `agent.session.replay`.
                    "query": query,
                    "sql": result.sql,
                    "rows_total": len(rows),
                    "columns": columns,
                    "result_id": result_id,
                    "chart": chart,
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
                result_id=result_id,
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
        already chosen from the query's shape. Keys: type (line, area, stacked_area, bar,
        stacked_bar, grouped_bar, hbar, donut, scatter, heatmap, gauge, sparkline, kpi,
        stat), x, y (measure column ids), series, target (gauge only). Use the ids
        run_query reported."""
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
                data={"tool": "set_chart", "ok": True, "chart": checked.model_dump()},
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
