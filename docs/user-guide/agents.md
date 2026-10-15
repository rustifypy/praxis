# Give tools to other agents

The **Agents** page hands this portal's query tools to other AI tools over **MCP** (Model Context
Protocol) — Claude Desktop, Claude Code, or any MCP client.

The tools are **read-only** and go through the same [query gate](../architecture/query-gate.md)
as a chat. No dashboards, no model edits, no Python.

| Tool | Does |
|---|---|
| `search_model` | Find measures and dimensions for a question |
| `describe_model` | One line per cube and view |
| `run_query` | Run a Cube JSON query; returns columns, the first 30 rows, row counts and the SQL. Capped at `MAX_ROWS` |

## Set it up

1. Open **Agents** and switch on **Serve tools over MCP**. The endpoint is
   `http://127.0.0.1:8420/mcp`.
2. Copy the configuration for your client. Both include the token.

=== "Claude Code"

    ```bash
    claude mcp add --transport http praxis http://127.0.0.1:8420/mcp \
      --header "Authorization: Bearer <token>"
    ```

=== "Claude Desktop"

    In `claude_desktop_config.json`:

    ```json
    {
      "mcpServers": {
        "praxis": {
          "command": "npx",
          "args": ["-y", "mcp-remote", "http://127.0.0.1:8420/mcp",
                   "--header", "Authorization: Bearer <token>"]
        }
      }
    }
    ```

The portal must be running for the tools to answer.


<div class="ui">
--8<-- "docs/.snippets/ui-agents.html"
<div class="ui-main narrow">
<div class="ui-card">
<div class="ui-h2">Agent access <span class="ui-faint">ⓘ</span></div>
<div class="ui-row" style="margin:10px 0"><span class="ui-switch"><i></i>Serve tools over MCP</span><span class="ui-faint">http://127.0.0.1:8420/mcp</span></div>
<p class="ui-muted">search_model, describe_model, run_query — read-only, and the portal must be running for them to answer.</p>
<div class="ui-spread" style="margin:10px 0 2px"><span class="ui-faint">Token</span><span class="ui-row"><span class="ui-btn">Show</span><span class="ui-btn">Rotate</span><span class="ui-btn">Copy</span></span></div>
<div class="ui-code">••••••••••••••••••••••••••••••••</div>
<div class="ui-spread" style="margin:10px 0 2px"><span class="ui-faint">Claude Code</span><span class="ui-btn">Copy</span></div>
<div class="ui-code">claude mcp add --transport http praxis http://127.0.0.1:8420/mcp --header "Authorization: Bearer ••••••••••••••••"</div>
</div>
</div>
</div>
</div>
<p class="ui-caption">The Agents page with MCP switched on. The token stays hidden until you press <b>Show</b>.</p>

## The token

The MCP token is separate from the portal's browser token and **survives restarts**. **Rotate**
(type `rotate`) makes a new one and breaks every configuration already pasted anywhere until each
is updated. Switching MCP off makes the endpoint answer 404.
