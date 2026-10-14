# User guide

This section is for people using the portal in a browser. It assumes setup is done — if not, start with
[Get started](../getting-started/index.md).

The rail on the left switches between five modes:

| Rail | What it is for |
|---|---|
| **Chat** | Ask a question in plain English. Get a checked query, a table, a chart and every step the agent took |
| **Boards** | Dashboards: questions you want to see again, refreshed with one button |
| **Data** | The connection, the data model and the model editor |
| **Flows** | Repeatable work in numbered steps, with a person checking each step |
| **Agents** | Give other AI tools (Claude Desktop, Claude Code) the same safe, read-only access |


<div class="ui">
--8<-- "docs/.snippets/ui-chat.html"
<aside class="ui-side">
<div class="ui-side-head"><span>Chats</span><span class="ui-btn ghost">+</span></div>
<div class="ui-item on">Revenue by region last quarter<small>today</small></div>
<div class="ui-item">North stores week over week<small>yesterday</small></div>
<div class="ui-item">Stock cover under 3 days<small>Mon</small></div>
<div class="ui-item">Top 10 items by margin<small>last week</small></div>
</aside>
<div class="ui-main">
<div class="ui-user">Which North stores drove the change?</div>
<div class="ui-tool"><div class="ui-tool-head"><span class="ui-mark">✓</span><span class="ui-tool-label">Total revenue by store, week · last 14 days, 1 filter</span><span class="ui-tool-summary">58 rows · 1.2s ▸</span></div></div>
<div class="ui-answer">Three stores account for most of North's rise: <strong>S014 Leeds Central</strong> (+£41k), S007 York (+£22k) and S021 Harrogate (+£18k)…</div>
<div class="ui-composer"><span>Ask about your data…</span><span class="ui-btn primary">↑</span></div>
</div>
</div>
</div>
<p class="ui-caption">The rail on the left switches mode; the column beside it belongs to the mode you are in.</p>

`Cmd/Ctrl + K` starts a new chat from anywhere.

| | Page | |
|---|---|---|
| **Everyday use** | [Ask questions](chat.md) | Everything a chat shows you, uploads and Python |
| | [Dashboards](boards.md) | Tiles, layout, refresh, chart types |
| | [Run a Flow](running-flows.md) | Briefs, the run story, review points, re-running |
| **Looking after it** | [Change the model](editing-the-model.md) | Branches, the editor, Save → Validate → Publish → Merge, rebuilding |
| | [Give tools to other agents](agents.md) | MCP setup |
