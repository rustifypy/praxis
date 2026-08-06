import json
from dataclasses import dataclass, field

from langchain.tools import tool

from ..chat.events import Event, emit
from ..cube import gate
from ..cube.catalog import Catalog
from ..cube.client import CubeClient
from ..cube.describe import describe_query
from ..grounding import Grounding
from ..retrieval import LexicalRetriever, render


@dataclass
class QueryRecord:
    query: dict
    sql: str | None
    row_count: int
    duration_ms: int | None
    warnings: list[str]


@dataclass
class Session:
    """Per-request state. Holds provenance so answers can be audited."""

    catalog: Catalog
    client: CubeClient
    grounding: Grounding
    max_rows: int = 5_000
    executed: list[QueryRecord] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


def build_tools(session: Session, preview_rows: int = 30) -> list:
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
                    # The browser shows the same preview the model sees. Full result
                    # sets stay out of the event log until charts land.
                    "preview": rows[:preview_rows],
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

    return [search_model, run_query, load_grounding, explain_last_query]
