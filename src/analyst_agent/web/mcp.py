"""The read-only tool subset, for agents that are not this one.

Three tools and no state. An MCP session is per-connection and ends without notice, so holding
a `Session` between calls would be the first thing in this process that leaks; instead every
call builds one from `app.state` and throws it away, and `run_query` returns the SQL inline
rather than leaving it somewhere for a second call to ask about. That is what makes
`explain_last_query` unnecessary here rather than merely absent.

What is exposed is what is safe to expose without a browser: reading the model, and running a
query through the same four-tier gate a chat turn uses. The dashboard, sandbox and editor
toolsets are not here — they author state whose invariants assume an event stream, an approval
click, or a container that only one checkout can serve.
"""

import json
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, Request
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from ..agent.retrieval import LexicalRetriever, render
from ..core.config import settings
from ..core.db import connect
from ..cube import gate
from ..cube.shape import columns_of
from ..sources import grounding

router = APIRouter(prefix="/api/mcp")

INSTRUCTIONS = """Query one database through a Cube semantic layer.

Call `search_model` first: it returns the measure and dimension ids a question can reach, and
queries are written against those ids rather than against table or column names. `run_query`
takes Cube JSON and returns rows with the SQL that produced them. There is no SQL entry point —
a query that does not pass the gate comes back as an explanation of what to fix."""

PREVIEW_ROWS = 30
"""Rows returned inline. An MCP client pays per token exactly as the local agent does, and the
whole set is not addressable here — there is no chat to store it under."""


@dataclass
class Access:
    """The stored row. Held on `app.state.mcp` and written through, so the guard tests one
    in-memory copy per request rather than opening the database on every tool call."""

    enabled: bool
    token: str


async def load() -> Access:
    """The one row, created with a fresh token the first time it is asked for. Callers must run
    after a store's `init()` — `core.db.SCHEMA` is what creates the table."""
    async with connect(settings.db_path) as db:
        cursor = await db.execute("SELECT enabled, token FROM mcp WHERE id = 1")
        row = await cursor.fetchone()
        if row is not None:
            return Access(enabled=bool(row["enabled"]), token=row["token"])
        access = Access(enabled=True, token=secrets.token_urlsafe(24))
        now = time.time()
        await db.execute(
            "INSERT INTO mcp (id, enabled, token, created_at, updated_at) VALUES (1, ?, ?, ?, ?)",
            (int(access.enabled), access.token, now, now),
        )
        await db.commit()
        return access


async def save(access: Access) -> None:
    async with connect(settings.db_path) as db:
        await db.execute(
            "UPDATE mcp SET enabled = ?, token = ?, updated_at = ? WHERE id = 1",
            (int(access.enabled), access.token, time.time()),
        )
        await db.commit()


def client_config(access: Access, base_url: str) -> dict:
    """What to paste, in the two shapes people actually need — and derived here rather than in
    the browser so that the endpoint and the header have one definition.

    `base_url` is the address this request arrived on, not `settings.web_host` — reach the
    portal over a tunnel or a LAN address and the settings pair still says 127.0.0.1, which is
    a config that cannot work from wherever the person actually is.

    `mcp-remote` is the shim because Claude Desktop's own connectors expect OAuth while this
    takes a bearer token; Cube's documented config has the same shape for the same reason.
    """
    url = f"{base_url.rstrip('/')}/mcp"
    header = f"Authorization: Bearer {access.token}"
    desktop = {
        "mcpServers": {
            "analyst": {
                "command": "npx",
                "args": ["-y", "mcp-remote", url, "--header", header],
            }
        }
    }
    return {
        "url": url,
        "desktop": json.dumps(desktop, indent=2),
        "cli": f'claude mcp add --transport http analyst {url} --header "{header}"',
    }


class Toggle(BaseModel):
    enabled: bool


@router.get("")
async def status(request: Request) -> dict:
    """Everything the panel draws: the switch, the token, what to paste, and the tool list read
    off the server itself rather than named a second time here."""
    access: Access = request.app.state.mcp
    server: MCPServer = request.app.state.mcp_server
    tools = await server.list_tools()
    return {
        "enabled": access.enabled,
        "token": access.token,
        "tools": [{"name": t.name, "description": t.description or ""} for t in tools],
        **client_config(access, str(request.base_url)),
    }


@router.post("/enabled")
async def set_enabled(body: Toggle, request: Request) -> dict:
    access: Access = request.app.state.mcp
    access.enabled = body.enabled
    await save(access)
    return {"enabled": access.enabled}


@router.post("/rotate")
async def rotate(request: Request) -> dict:
    """A new token, which invalidates every config already pasted anywhere. That is the point —
    it is how access is taken back — so the panel says so before it is clicked."""
    access: Access = request.app.state.mcp
    access.token = secrets.token_urlsafe(24)
    await save(access)
    return {"token": access.token, **client_config(access, str(request.base_url))}


def build(app) -> MCPServer:
    """The server, reading its catalog off the portal's own `app.state.cube` — one cached
    catalog for the process, so an MCP call cannot query a model the portal is not serving."""
    mcp = MCPServer(name="analyst", instructions=INSTRUCTIONS)

    @mcp.tool()
    async def search_model(question: str) -> str:
        """Find the measures and dimensions available for a question. Returns member ids,
        types, notes, and the valid filter operators for each. Call this before building
        any query."""
        catalog = await app.state.cube.catalog()
        hits = LexicalRetriever(catalog).search(question)
        if not hits:
            return "The semantic model is empty."
        out = render(hits)
        certified = grounding.load().render_certified(question)
        if certified:
            out += f"\n\n# Certified queries for similar requests\n\n{certified}"
        return out

    @mcp.tool()
    async def run_query(query: dict) -> str:
        """Execute a Cube JSON query. Keys: measures, dimensions, timeDimensions, filters,
        segments, order, limit, offset. Returns rows and the SQL that produced them, or a
        rejection explaining what to fix."""
        catalog = await app.state.cube.catalog()
        result = await gate.run(query, catalog, app.state.cube, app.state.mcp_max_rows)
        if not result.ok:
            return result.as_message()

        rows = result.rows or []
        columns = columns_of(query, catalog)
        head = rows[:PREVIEW_ROWS]
        body = {
            "columns": [{"id": c.id, "label": c.label, "kind": c.kind} for c in columns],
            "rows": head,
            "rows_returned": len(head),
            "rows_total": len(rows),
            "sql": result.sql,
        }
        # Inline, because there is no second call that could ask for it: an MCP session does
        # not outlive the request reliably enough to be asked "what was that query?".
        out = json.dumps(body, indent=2, default=str)
        if result.warnings:
            out += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in result.warnings)
        return out

    @mcp.tool()
    async def describe_model() -> str:
        """List every cube and view in the semantic model, with its member count. The map of
        what can be asked about at all, where search_model answers one question."""
        catalog = await app.state.cube.catalog()
        if not catalog.entities:
            return "The semantic model is empty. Nothing is being served."
        lines = []
        for entity in catalog.entities:
            kind = "view" if entity.is_view else "cube"
            note = f" — {entity.description}" if entity.description else ""
            lines.append(f"- {entity.name} ({kind}, {len(entity.members)} members){note}")
        return "\n".join(lines)

    return mcp
