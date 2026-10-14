# Dashboards

A dashboard (**Boards** on the rail) is a set of questions you want to see again.

**A tile stores the query, not the rows.** Refreshing runs every query again, so a tile never
shows data from a query that has quietly stopped matching the model. A window like *last 30
days* moves with each refresh; a fixed range like *2026-01-01 → 2026-01-31* does not. Each tile
shows the dates it covers.


<div class="ui">
--8<-- "docs/.snippets/ui-dashboards.html"
<aside class="ui-side">
<div class="ui-side-head"><span>Dashboards</span><span class="ui-btn ghost">+</span></div>
<div class="ui-item on">Weekly trading</div>
<div class="ui-item">Stock health</div>
<div class="ui-item">North region</div>
</aside>
<div class="ui-main">
<div class="ui-spread"><div class="ui-h1" style="margin:0">Weekly trading</div><span class="ui-row"><span class="ui-btn primary">Refresh</span><span class="ui-btn ghost">⋯</span></span></div>
<div class="ui-tiles">
<div class="ui-tile"><div class="ui-tile-title">Revenue, last 7 days</div><div class="ui-kpi">£3.61M</div><div class="ui-faint">2026-10-07 → 2026-10-13</div></div>
<div class="ui-tile"><div class="ui-tile-title">Units sold, last 7 days</div><div class="ui-kpi">412,880</div><div class="ui-faint">2026-10-07 → 2026-10-13</div></div>
<div class="ui-tile wide"><div class="ui-tile-title">Daily revenue, last 90 days</div><svg class="ui-line" viewBox="0 0 300 90" preserveAspectRatio="none"><polyline fill="none" stroke="currentColor" stroke-width="2" points="0,62 25,58 50,64 75,50 100,54 125,40 150,46 175,34 200,38 225,26 250,30 275,18 300,22"/></svg><div class="ui-faint">2026-07-16 → 2026-10-13</div></div>
<div class="ui-tile"><div class="ui-tile-title">Revenue by region</div><div class="ui-bars" style="border:0;height:90px;padding:0"><div><i style="height:88%"></i><span>N</span></div><div><i style="height:72%"></i><span>S</span></div><div><i style="height:68%"></i><span>E</span></div><div><i style="height:54%"></i><span>W</span></div></div></div>
<div class="ui-tile"><div class="ui-tile-title">Top items by margin</div><div class="ui-faint" style="padding:18px 0">Not refreshed yet — press Refresh to load it.</div></div>
</div>
</div>
</div>
</div>
<p class="ui-caption">A board of five tiles. The newest has no data until someone presses <b>Refresh</b>.</p>

## Making one

1. Click **+** under **Dashboards**. A board called *New dashboard* opens. **Rename** it.
2. Go to **Chat**, ask the question you want to keep, then say *"save that to the Sales
   dashboard"*.
3. Back on the board, press **Refresh**. A just-added tile has no data until someone refreshes:
   *Not refreshed yet — press Refresh to load it.*

The agent can create boards, add, update and remove tiles, and arrange them. It **cannot**
refresh — refresh is a button, so "tidy up this board" can never turn into a dozen warehouse
queries.

## Refresh is all or nothing

Every tile is compiled before any runs. If one tile no longer compiles against the model, nothing
executes and every tile keeps its last numbers. The board says which tiles are broken; ask in chat
to fix or remove them. The same check runs each time you open the board.

## Layout

- Drag a tile by its grip **⠿**, or focus it and use the arrow keys.
- Click a tile's width chip to change how many columns it spans.
- Choose **1 column**, **2 columns**, … for the whole board.
- **Undo** steps back. Every layout change is saved as a new version, so undo never loses one.

## Chart types

| Type | Use for | Needs |
|---|---|---|
| `line`, `area`, `stacked_area` | a measure over time | a time grain; area types need one |
| `bar`, `stacked_bar`, `grouped_bar`, `hbar` | comparing categories | up to 25 bars (30 for `hbar`) |
| `donut` | shares of a whole | up to 8 slices |
| `scatter` | two measures against each other | a measure on x |
| `heatmap` | two dimensions and a measure | a series |
| `kpi`, `stat` | one headline number, with change | `stat` needs a time grain |
| `sparkline` | a small trend | a time grain |
| `gauge` | one value against a target | one row and a target |
| `table` | anything | — |

Series are capped at 8 and points at 100. A result that does not fit its chart is drawn as a
table, with the reason.
