# API

The portal serves two APIs from one process:

| | For | Auth |
|---|---|---|
| [HTTP](http.md) — `/api/*` | the browser app | portal token: `?token=` or the `analyst_token` cookie |
| [MCP](mcp.md) — `/mcp` | other AI agents | `Authorization: Bearer <MCP token>` |

The HTTP API is the browser app's own interface, not a versioned public API: it can change with
any release. For programmatic access to your data, use MCP.

FastAPI's own interactive docs are served at `/docs` and `/openapi.json`, behind the portal token.
