# Ask questions

*Questions become governed Cube queries — never raw SQL. Every step shows up below, including the
ones the gate rejects.*

## How a question is answered

The agent never writes SQL. It searches the model, writes a small **Cube JSON query** naming
members from it, and that query passes four checks before it touches your warehouse:

| Check | What | Cost |
|---|---|---|
| 1. Shape | Is it a valid Cube query? | free |
| 2. Meaning | Do the names exist? Does each filter fit its type? Is the time grain available? | free |
| 3. Compile | Can Cube turn it into SQL? Nothing runs yet | one small call |
| 4. Run | Run it, capped at 5,000 rows | warehouse |

A failed check comes back to the agent as a message it can act on — *"`orders.cnt` does not
exist. Closest matches: orders.count"* — and it corrects the query. You see the rejection and
the retry.

## What the transcript shows

| Block | What you see |
|---|---|
| **Thinking** | The model's reasoning summary. Collapsible; *Thought for Xs* when done |
| **Search** | What it looked up in the model, and how many members matched |
| **Query** | A plain description of the query, rows and time. Expand for the **Cube query**, the **SQL** Cube made, warnings and errors, each with Copy |
| **Result** | A sortable table (*N of M rows*, **Show all**, **CSV** download) and a chart when one fits |
| **Python** | The script, its approval, its live output, and any files it wrote |
| **Answer** | The written answer, with **Copy answer** |

A chart is chosen from the result's shape. *No chart* is a normal answer: some results are only
a table. You can ask for a different chart ("show that as a stacked bar").

Sorting a result sorts the rows loaded, not every row: the note says *Sorted over the N rows
loaded, not all M* when it matters.

## Files and Python

Attach a file with **+** or drag it onto the window. It goes to the sandbox, which reads its
shape — rows, columns, types — for the agent. Up to 100 MB, any type.

The agent can run Python in the sandbox for what a query cannot express: ratios, period-over-period
changes, shares, joining your uploaded file to query results. It moves a query's stored rows there
with `export_result`.

Each script is shown before it runs: **Run this script?**

<div class="ui">
--8<-- "docs/.snippets/ui-chat.html"
<div class="ui-main">
<div class="ui-user">What share of each region's revenue came from Produce, this week vs last?</div>
<div class="ui-tool"><div class="ui-tool-head"><span class="ui-mark">✓</span><span class="ui-tool-label">Total revenue by region, category, day · last 14 days</span><span class="ui-tool-summary">392 rows · 1.1s ▸</span></div></div>
<div class="ui-tool"><div class="ui-tool-head"><span class="ui-mark">✓</span><span class="ui-tool-label">sales_14d.csv</span><span class="ui-tool-summary">0.4s</span></div></div>
<div class="ui-tool wait"><div class="ui-tool-head"><span class="ui-mark">⏸</span><span class="ui-tool-label">produce_share.py</span><span class="ui-tool-summary">waiting for you ▾</span></div>
<div class="ui-tool-body">
<div class="ui-code">import pandas as pd

df = pd.read_csv("uploads/sales_14d.csv")
day = pd.to_datetime(df["daily_sales.sale_date.day"])
df["week"] = (day &gt; day.max() - pd.Timedelta(days=7)).map({True: "this", False: "last"})

keys = ["daily_sales.dim_stores_region", "week"]
total = df.groupby(keys)["daily_sales.total_revenue"].sum()
produce = df[df["daily_sales.dim_items_category"] == "Produce"]
share = produce.groupby(keys)["daily_sales.total_revenue"].sum() / total
print(share.unstack().round(3))</div>
<div class="ui-ask"><span>Run this script?</span><span class="ui-btn primary">Run</span><span class="ui-btn">Always in this chat</span><span class="ui-btn">Decline</span></div>
</div></div>
</div>
</div>
</div>
<p class="ui-caption">A script waiting for approval. The whole script is shown; nothing runs until you choose.</p>


- **Run** — run it once;
- **Always in this chat** — stop asking in this chat until the portal restarts;
- **Decline** — the agent is told you said no.

Images a script makes show inline; CSVs and text show a preview and a **Download** link.

## Controls

- **Enter** sends; **Shift + Enter** is a new line.
- **Stop** ends the agent's turn. A script running in the sandbox is killed.
- Long chats are summarised to fit the model's context; a notice says when.
- Rename a chat by double-clicking it; delete with ×, then **Delete**.

## Saving to a dashboard

There is no button for this. Ask in the chat: *"save that to the Sales dashboard"*. The agent adds
a tile with the query — see [Dashboards](boards.md).
