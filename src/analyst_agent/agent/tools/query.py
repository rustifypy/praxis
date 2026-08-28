import json
from dataclasses import asdict

from langchain.tools import tool

from ...core import transcript
from ...modelstore import gate
from ...modelstore.charts import MAX_POINTS, chart_for, validate_spec
from ...modelstore.shape import Column, columns_of, describe_query
from ..retrieval import LexicalRetriever, render
from ..session import QueryRecord, Session
from .trace import end, start

EVENT_KEYS = frozenset(
    {
        "tool",
        "label",
        "ok",
        "hits",
        "errors",
        "query",
        "sql",
        "rows_total",
        "columns",
        "result_id",
        "chart",
        "preview",
        "duration_ms",
        "warnings",
    }
)
"""Every key a query tool's event may carry, asserted by a test. Unlike the other three
toolsets these do carry state — a replayed transcript draws the table and the chart from
`tool_end` alone, which is what `preview`, `columns` and `chart` are for."""


def build_query_tools(
    session: Session, preview_rows: int = 30, event_rows: int = MAX_POINTS
) -> list:
    retriever = LexicalRetriever(session.catalog)

    @tool
    def search_model(question: str) -> str:
        """Find the measures and dimensions available for a question. Returns member
        ids, types, notes, and the valid filter operators for each. Call this before
        building any query."""
        start("search_model", question)
        hits = retriever.search(question)
        end("search_model", True, hits=len(hits))
        if not hits:
            return "The semantic model is empty."
        return render(hits)

    @tool
    async def run_query(query: dict) -> str:
        """Execute a Cube JSON query. Keys: measures, dimensions, timeDimensions,
        filters, segments, order, limit, offset. Returns rows, or a rejection
        explaining what to fix."""
        start("run_query", describe_query(query, session.catalog), query=query)
        result = await gate.run(query, session.catalog, session.client, session.max_rows)

        if not result.ok:
            session.rejected.append({"query": query, "errors": result.errors})
            # A rejection is a normal step in the trace, not an error event — the agent
            # reads the reason and retries, and the user should see that it happened.
            end("run_query", False, query=query, errors=result.errors)
            return result.as_message() + "\n\nFix the query and call run_query again."

        rows = result.rows or []
        # `asdict` for the columns, which are dataclasses; `model_dump` for the chart, which
        # is a pydantic model because `set_chart` is handed one by the agent.
        columns = [asdict(column) for column in columns_of(query, session.catalog)]
        picked = chart_for(query, session.catalog, rows)
        chart = picked.model_dump() if picked else None
        # The handle the browser fetches the whole result by. The event carries only a
        # head of the rows — enough to render a table the moment a transcript replays,
        # without putting a 5000-row payload through the event log on every reload.
        result_id = ""
        if session.chat_id:
            result_id = await transcript.save_result(
                session.chat_id, session.turn_id, columns, rows
            )
        # Kept in hand as well as saved, because a Phase saves nothing: a Run has no `results`
        # row, so without this `export_result` would have no rows to write and the query→Python
        # bridge would close for exactly the case Flows need it open.
        session.rows = rows
        end(
            "run_query",
            True,
            # Repeated from tool_start so a turn can be rebuilt from tool_end alone — see
            # `agent.session.replay`.
            query=query,
            sql=result.sql,
            rows_total=len(rows),
            columns=columns,
            result_id=result_id,
            chart=chart,
            preview=rows[:event_rows],
            duration_ms=result.duration_ms,
            warnings=result.warnings,
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
    def set_chart(spec: dict) -> str:
        """Replace the chart drawn for the most recent result. Rarely needed — one is
        already chosen from the query's shape. Keys: type (line, area, stacked_area, bar,
        stacked_bar, grouped_bar, hbar, donut, scatter, heatmap, gauge, sparkline, kpi,
        stat), x, y (measure column ids), series, target (gauge only). Use the ids
        run_query reported."""
        start("set_chart", f"Charting as {spec.get('type', '?')}")
        if not session.executed:
            end("set_chart", False)
            return "No query has run yet, so there is nothing to chart."

        columns = [Column(**raw) for raw in session.executed[-1].columns]
        checked, errors = validate_spec(spec, columns)
        if checked is None:
            # A rejection is text the agent can act on, exactly as the query gate does.
            end("set_chart", False, errors=errors)
            listed = ", ".join(f"{column.id} ({column.kind})" for column in columns)
            return (
                "Chart rejected:\n"
                + "\n".join(f"- {error}" for error in errors)
                + f"\n\nColumns in this result: {listed}"
            )

        end("set_chart", True, chart=checked.model_dump())
        return f"The result is now drawn as a {checked.type} chart."

    @tool
    def explain_last_query() -> str:
        """Show the SQL Cube generated for the most recent successful query."""
        start("explain_last_query", "Reading the last query")
        end("explain_last_query", bool(session.executed))
        if not session.executed:
            return "No query has run yet."
        last = session.executed[-1]
        return f"```json\n{json.dumps(last.query, indent=2)}\n```\n\n```sql\n{last.sql}\n```"

    return [search_model, run_query, set_chart, explain_last_query]
