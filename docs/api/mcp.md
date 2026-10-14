# MCP

The portal is an MCP server named `analyst`, over **streamable HTTP** at `/mcp`. Setup from the
browser is on the [Agents page](../user-guide/agents.md).

## Auth

`Authorization: Bearer <MCP token>`. The token is stored in `analyst.db`, survives restarts, and
is separate from the portal's browser token. A missing or wrong token gets `401`. When MCP is
switched off, `/mcp` answers `404`.

## Server instructions

> Query one database through a Cube semantic layer.
>
> Call `search_model` first: it returns the measure and dimension ids a question can reach, and
> queries are written against those ids rather than against table or column names. `run_query`
> takes Cube JSON and returns rows with the SQL that produced them. There is no SQL entry point —
> a query that does not pass the gate comes back as an explanation of what to fix.

## Tools

All three are read-only and use the served model on `main`.

### `search_model(question: str)`

Lexical search over the served model. Returns member ids, types, descriptions, agent notes and
valid filter operators.

### `describe_model()`

One line per cube and view: `- name (cube|view, N members) — description`.

### `run_query(query: dict)`

A Cube JSON query — `measures`, `dimensions`, `timeDimensions`, `filters`, `segments`, `order`,
`limit`, `offset` — run through the full [query gate](../architecture/query-gate.md), capped at
`MAX_ROWS`.

On success it returns JSON:

```json
{
  "columns": [{"id": "orders.status", "label": "Status", "kind": "dimension"}],
  "rows": [{"orders.status": "paid", "orders.count": 1204}],
  "rows_returned": 3,
  "rows_total": 3,
  "sql": "SELECT ..."
}
```

`rows` holds at most the first 30; `rows_total` is how many the query returned. Gate warnings
follow the JSON. A rejected query returns the gate's explanation — with closest member names —
instead, so the calling agent can correct it.

## Clients

```bash
# Claude Code
claude mcp add --transport http analyst http://127.0.0.1:8420/mcp \
  --header "Authorization: Bearer <token>"
```

Claude Desktop connects through `mcp-remote`, because its connectors expect OAuth; the Agents
page shows the exact `claude_desktop_config.json` block.
