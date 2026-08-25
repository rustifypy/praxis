"""The MCP server itself: the three tools something else may call, and what they answer.

An entry point and not a controller — nothing in the portal calls these. `server.py` builds the
server, mounts its ASGI app at `/mcp`, and the request guard checks the bearer token before any of
it is reached.

Every tool reads its catalog off the portal's own `app.state.cube`, so an MCP call cannot query a
model the portal is not serving. **Read-only by construction**: `search_model`, `run_query` and
`describe_model` are the half of the query agent's vocabulary that writes nothing — no script runs,
no dashboard is authored, no model file is touched.

Rows come back inline because there is no second call that could ask for them: an MCP session does
not reliably outlive the request, so there is no chat to store a result under and nothing to
address it by.
"""

import json

from mcp.server.mcpserver import MCPServer

from ...agent.retrieval import LexicalRetriever, render
from ...modelstore import gate
from ...modelstore.shape import columns_of
from ..support import state

INSTRUCTIONS = """Query one database through a Cube semantic layer.

Call `search_model` first: it returns the measure and dimension ids a question can reach, and
queries are written against those ids rather than against table or column names. `run_query`
takes Cube JSON and returns rows with the SQL that produced them. There is no SQL entry point —
a query that does not pass the gate comes back as an explanation of what to fix."""

# Rows returned inline: an MCP client pays per token as the local agent does, and the whole set
# is not addressable here — there is no chat to store it under.
PREVIEW_ROWS = 30


def build(app) -> MCPServer:
    # Reads its catalog off the portal's own `app.state.cube`, so an MCP call cannot query a
    # model the portal is not serving.
    mcp = MCPServer(name="analyst", instructions=INSTRUCTIONS)

    @mcp.tool()
    async def search_model(question: str) -> str:
        """Find the measures and dimensions available for a question. Returns member ids,
        types, notes, and the valid filter operators for each. Call this before building
        any query."""
        catalog = await (await state.cube(app)).catalog()
        hits = LexicalRetriever(catalog).search(question)
        if not hits:
            return "The semantic model is empty."
        out = render(hits)
        return out

    @mcp.tool()
    async def run_query(query: dict) -> str:
        """Execute a Cube JSON query. Keys: measures, dimensions, timeDimensions, filters,
        segments, order, limit, offset. Returns rows and the SQL that produced them, or a
        rejection explaining what to fix."""
        catalog = await (await state.cube(app)).catalog()
        result = await gate.run(query, catalog, await state.cube(app), app.state.mcp_max_rows)
        if not result.ok:
            return result.as_message()

        rows = result.rows or []
        columns = columns_of(query, catalog)
        head = rows[:PREVIEW_ROWS]
        body = {
            "columns": [
                {"id": column.id, "label": column.label, "kind": column.kind} for column in columns
            ],
            "rows": head,
            "rows_returned": len(head),
            "rows_total": len(rows),
            "sql": result.sql,
        }
        # Inline, because there is no second call that could ask for it: an MCP session does
        # not reliably outlive the request.
        out = json.dumps(body, indent=2, default=str)
        if result.warnings:
            out += "\n\nWarnings:\n" + "\n".join(f"- {warning}" for warning in result.warnings)
        return out

    @mcp.tool()
    async def describe_model() -> str:
        """List every cube and view in the semantic model, with its member count. The map of
        what can be asked about at all, where search_model answers one question."""
        catalog = await (await state.cube(app)).catalog()
        if not catalog.entities:
            return "The semantic model is empty. Nothing is being served."
        lines = []
        for entity in catalog.entities:
            kind = "view" if entity.is_view else "cube"
            note = f" — {entity.description}" if entity.description else ""
            lines.append(f"- {entity.name} ({kind}, {len(entity.members)} members){note}")
        return "\n".join(lines)

    return mcp
