"""Agent access: what something else gets to call, and the switch that lets it.

The read-only half of the query agent's vocabulary — `search_model`, `run_query`, the served
model — behind a token of its own, so turning it off is one flag and rotating it does not touch
the portal's. Off by default: this opens the warehouse to whatever holds the token.
"""

import json
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, Request
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel
from sqlalchemy import insert, select, update

from ..agent.retrieval import LexicalRetriever, render
from ..core.config import settings
from ..core.db import connect
from ..core.db import mcp as row
from ..cube import gate
from ..cube.shape import columns_of
from ..datamodel import grounding
from . import state

router = APIRouter(prefix="/api/mcp")

INSTRUCTIONS = """Query one database through a Cube semantic layer.

Call `search_model` first: it returns the measure and dimension ids a question can reach, and
queries are written against those ids rather than against table or column names. `run_query`
takes Cube JSON and returns rows with the SQL that produced them. There is no SQL entry point —
a query that does not pass the gate comes back as an explanation of what to fix."""

# Rows returned inline: an MCP client pays per token as the local agent does, and the whole set
# is not addressable here — there is no chat to store it under.
PREVIEW_ROWS = 30


@dataclass
class Access:
    # Held on `app.state.mcp` and written through, so the guard tests one in-memory copy per
    # request rather than opening the database on every tool call.
    enabled: bool
    token: str


async def load() -> Access:
    # Created with a fresh token the first time it is asked for. Callers must run after a
    # store's `init()`, which is what creates the table.
    async with connect(settings.db_path) as db:
        found = await db.execute(select(row.c.enabled, row.c.token).where(row.c.id == 1))
        stored = found.mappings().first()
        if stored is not None:
            return Access(enabled=bool(stored["enabled"]), token=stored["token"])
        access = Access(enabled=True, token=secrets.token_urlsafe(24))
        now = time.time()
        await db.execute(
            insert(row).values(
                id=1,
                enabled=int(access.enabled),
                token=access.token,
                created_at=now,
                updated_at=now,
            )
        )
        return access


async def save(access: Access) -> None:
    async with connect(settings.db_path) as db:
        await db.execute(
            update(row)
            .where(row.c.id == 1)
            .values(enabled=int(access.enabled), token=access.token, updated_at=time.time())
        )


def client_config(access: Access, base_url: str) -> dict:
    # `base_url` is the address this request arrived on and not `settings.web_host`: reached
    # over a tunnel or a LAN address, the settings pair still says 127.0.0.1, which is a config
    # that cannot work from wherever the person actually is. `mcp-remote` is the shim because
    # Claude Desktop's own connectors expect OAuth while this takes a bearer token.
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
    # The tool list is read off the server rather than named a second time here.
    access: Access = request.app.state.mcp
    server: MCPServer = request.app.state.mcp_server
    tools = await server.list_tools()
    return {
        "enabled": access.enabled,
        "token": access.token,
        "tools": [{"name": tool.name, "description": tool.description or ""} for tool in tools],
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
    # Invalidates every config already pasted anywhere, which is the point: it is how access is
    # taken back, so the panel says so before it is clicked.
    access: Access = request.app.state.mcp
    access.token = secrets.token_urlsafe(24)
    await save(access)
    return {"token": access.token, **client_config(access, str(request.base_url))}


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
        certified = (await grounding.load()).render_certified(question)
        if certified:
            out += f"\n\n# Certified queries for similar requests\n\n{certified}"
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
