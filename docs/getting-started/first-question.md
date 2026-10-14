# 5. Ask your first question

Step 5 of 5. Setup is finished, and the portal is on **Chat**.
{ .ax-step }


<div class="ui">
--8<-- "docs/.snippets/ui-chat.html"
<div class="ui-main narrow">
<div class="ui-h1">Ask about your data</div>
<p class="ui-muted">Questions become governed Cube queries — never raw SQL. Every step shows up below, including the ones the gate rejects.</p>
<div class="ui-faint">Try one of these</div>
<div class="ui-suggest">
<div class="ui-suggestion"><b>daily sales</b><span class="ui-tag accent">view</span><small>Units and revenue per store, item and day, with region and category.</small></div>
<div class="ui-suggestion"><b>stock position</b><span class="ui-tag accent">view</span><small>Closing stock per store and item, with days of cover.</small></div>
<div class="ui-suggestion"><b>fact transfers</b><span class="ui-tag">cube</span><small>Stock moved between stores, by day.</small></div>
<div class="ui-suggestion"><b>dim stores</b><span class="ui-tag">cube</span><small>6 members</small></div>
</div>
<div class="ui-composer"><span>Ask about your data…</span><span class="ui-btn primary">↑</span></div>
</div>
</div>
</div>
<p class="ui-caption">The welcome screen. Suggestions come from what your model actually serves, views first.</p>

The welcome screen suggests up to four questions based on your model. Click one, or type your
own:

> What was revenue by region last quarter?

You see each step as it happens:

1. the agent searching the model for the right measures and dimensions;
2. the Cube query it wrote — and, if the query was wrong, the rejection and the retry;
3. the SQL Cube made from it;
4. the result as a table and, where it fits, a chart.

<div class="ui">
--8<-- "docs/.snippets/ui-chat.html"
<div class="ui-main">
<div class="ui-user">What was revenue by region last quarter?</div>
<div class="ui-tool"><div class="ui-tool-head"><span class="ui-mark">✓</span><span class="ui-tool-label">revenue by region</span><span class="ui-tool-summary">6 matches</span></div></div>
<div class="ui-tool failed"><div class="ui-tool-head"><span class="ui-mark">✕</span><span class="ui-tool-label">Revenue by region, quarter · last quarter</span><span class="ui-tool-summary">rejected · retrying ▾</span></div>
<div class="ui-tool-body"><div class="ui-reasons">'daily_sales.revenue' does not exist. Closest matches: daily_sales.total_revenue</div></div></div>
<div class="ui-tool"><div class="ui-tool-head"><span class="ui-mark">✓</span><span class="ui-tool-label">Total revenue by region, quarter · last quarter</span><span class="ui-tool-summary">4 rows · 0.9s ▸</span></div></div>
<div class="ui-answer"><strong>North led last quarter with £4.82M</strong>, about 31% of the total. South and East were close behind at £3.95M and £3.71M; West trailed at £2.98M.</div>
<div class="ui-result">
<div class="ui-result-head"><span>4 rows</span><span>CSV</span></div>
<table class="ui-table"><thead><tr><th>Region</th><th>Quarter</th><th class="num">Total revenue</th></tr></thead>
<tbody><tr><td>North</td><td>2026 Q3</td><td class="num">4,821,400</td></tr><tr><td>South</td><td>2026 Q3</td><td class="num">3,952,180</td></tr><tr><td>East</td><td>2026 Q3</td><td class="num">3,708,650</td></tr><tr><td>West</td><td>2026 Q3</td><td class="num">2,981,020</td></tr></tbody></table>
<div class="ui-bars"><div><i style="height:88%"></i><span>North</span></div><div><i style="height:72%"></i><span>South</span></div><div><i style="height:68%"></i><span>East</span></div><div><i style="height:54%"></i><span>West</span></div></div>
</div>
<div class="ui-composer"><span>Ask about your data…</span><span class="ui-btn primary">↑</span></div>
</div>
</div>
</div>
<p class="ui-caption">One answer, top to bottom: the model search, a query the gate rejected with the nearest real name, the corrected query, the answer, and its rows.</p>


The agent never writes SQL. Every query is checked against the model before it reaches the
warehouse. More on what a chat shows in [Ask questions](../user-guide/chat.md).

## You are set up

| To | Go to |
|---|---|
| Keep a question and refresh it later | [Dashboards](../user-guide/boards.md) |
| Improve descriptions, add a measure or a view | [Change the model](../user-guide/editing-the-model.md) |
| Run work you repeat every week, step by step | [Run a Flow](../user-guide/running-flows.md), then [Flow authoring](../flows/index.md) |
| Give Claude Desktop or Claude Code the same access | [Give tools to other agents](../user-guide/agents.md) |
| Change settings, upgrade, or fix an error | [Operating](../operations/index.md) |
