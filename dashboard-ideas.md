# Dashboard: what it is, what constrains it, and what we could build

Working notes for making the dashboard a *dashboard* rather than n tiles and a paragraph.
Scope is the boards mode only — charts in the chat transcript are a separate surface and are
not covered here.

## Where it stands today

A board renders as: a header (title, source, tile count, last refresh), up to three banners,
one insight paragraph, and a CSS grid of tiles. A tile is a title, a resolved-window label,
and either a chart, a KPI row or a 12-row table. There is no per-tile control, no
interaction beyond a chart tooltip, and no way to rearrange anything from the page.

| piece | file |
|---|---|
| the board, the grid, the only Refresh | `src/web/src/components/Dashboard.tsx` |
| one tile | `src/web/src/components/Tile.tsx` |
| line / bar / kpi as inline SVG | `src/web/src/components/ResultChart.tsx` |
| the board list column | `src/web/src/components/DashboardNav.tsx` |
| cell and tick formatting | `src/web/src/format.ts` |
| styles | `src/web/src/styles.css` (dashboards block ~L3740) |
| what a valid tile is | `src/analyst_agent/dashboards/tiles.py` |
| placement | `src/analyst_agent/dashboards/layout.py` |
| run-to-run change | `src/analyst_agent/dashboards/delta.py` |
| the summary paragraph | `src/analyst_agent/dashboards/insights.py` |
| the atomic refresh | `src/analyst_agent/dashboards/refresh.py` |
| tables in `analyst.db` | `src/analyst_agent/dashboards/store.py` |
| HTTP surface | `src/analyst_agent/web/dashboards.py` |
| the eight agent tools | `src/analyst_agent/agent/tools/dashboards.py` |

## Constraints that decide what is cheap and what is not

**`core.db.SCHEMA` will never gain a column.** `CREATE TABLE IF NOT EXISTS` does not diff, so
a new table appears on a live `analyst.db` and a new column silently does not. But
`tiles.chart` and `layouts.spec` are already JSON blobs — **that is the extension seam.**
Per-tile options (colour, threshold, target, compare, format, sparkline) and per-board
structure (sections, heights, tabs) can all grow there without touching the schema.

**Renderer branch first, `CHART_TYPES` tuple second.** `tiles.CHART_TYPES` is
`line|bar|kpi|table` — exactly what `ResultChart` draws. A validator that accepts a chart the
renderer cannot draw makes the tile misrepresent itself. Every new chart type is three
changes in order: a `ResultChart` branch, a `validate_spec` case, a `chart_for` heuristic.

**A refresh is atomic, and it is a button.** Every tile compiles before any tile executes, so
per-tile progress would display the partial state the refusal exists to prevent. The
invariant is that *the agent* cannot trigger a refresh (a "tidy this up" turn would become
two dozen warehouse queries) — **the page always could**, so an opt-in page timer breaks
nothing.

**A tool event carries only intent.** `agent/tools/dashboards._start`/`_end` have no parameter
that could carry a layout, a tile list or a chart spec; `EVENT_KEYS` asserts it. The row is
the truth. Anything the agent edits is read back from the store, never from the event.

**A chart is chosen on the data, so it cannot be validated once.** `tiles.validate` at
authoring, `tiles.outgrown` on every refresh and every read. A tile authored as twelve bars
in January is four hundred by December.

**Impact and provenance stay honest.** `resolved_ranges` is the only thing that says whether
a window moved; `refresh.source_mismatch` is the only thing between a stored query and a
number from the wrong warehouse. Neither may be bypassed by a presentation feature.

---

## The menu

### A. Already paid for — the storage exists and nothing uses it

1. **Drag to rearrange.** `POST /{id}/layout` validates through the same `layout.validate` the
   agent's `set_layout` uses, and `api.ts` already exports `setLayout` — no component calls
   it. Layouts are append-only so undo is free and `revert_layout` already exists.
2. **A board-level date control.** `tiles.time_member` exists in the schema *for this* and is
   unread today. A range picker in the header, injected into each tile's `timeDimension` at
   refresh; tiles with no `time_member` say so rather than pretending. `resolved_ranges`
   keeps each tile's label truthful under an override.
3. **Per-tile insights.** `tile_results.insight` is reserved. Rejected once as "twelve model
   calls" — the middle path is **one call returning a JSON object keyed by tile id**, same
   cost as today's paragraph, stored per tile.
4. **KPI sparkline over refresh history.** `ROWS_KEPT = 2` is a constant and retention is
   deliberately a DELETE so the depth stays changeable. Raise it (or prune conditionally on
   `row_count == 1`) and every KPI gets a trend line from data already stored.

### B. Chart vocabulary — **all built**

`CHART_TYPES` is now line, area, stacked_area, bar, stacked_bar, grouped_bar, hbar, donut,
scatter, heatmap, gauge, sparkline, kpi, stat, table. Every one has a `ResultChart` branch, a
`validate_spec` rule and, where the verdict depends on the data, an `outgrown` rule.

**None of them is picked automatically.** `chart_for` still returns only line, bar or kpi, so
a chat result renders exactly as it always did; the rest arrive from `set_chart` or a tile.
Both tool docstrings carry the vocabulary.

`ChartSpec` gained one field, `target`, rejected on anything but a gauge. `validate_spec` is
now driven by four tuples — `NO_X`, `TIME_X`, `MEASURE_X`, `WITH_SERIES`/`NEEDS_SERIES` — so
a new type declares its axis rules rather than adding a branch.

5. Area / stacked area — share of total over time.
6. Stacked & grouped bar — the series column is currently *thrown away* for bars; only
   `series[0]` is drawn.
7. Horizontal bar — the real fix for label truncation; category names read at any length.
8. Donut / pie — resisted for good reasons; expected on a board for 3–5 slices.
9. Scatter — two measures, no time. Currently inexpressible.
10. Sparkline — a chart type *and* a tile decoration.
11. Heatmap / calendar — two dimensions and a measure, which the gate already produces.
12. Gauge / progress-to-target — needs a `target` in the chart blob.
13. **Big-number + trend combo** — value, delta chip, sparkline in one tile. Probably the
    highest-value new type.

### C. Making one tile say more

14. **Delta on every tile.** `delta.py` is run-to-run and single-row only; an *in-series*
    delta (last bucket vs prior) needs no history and works on every time series.
15. Compare to previous period — run the query twice with a shifted range, draw a ghost
    line. `compare: "previous_period"` in the chart blob; 2× queries per refresh.
16. Thresholds and conditional colour — `{good_above: …}` in the blob. Colour never the only
    cue; pair with an icon.
17. Per-tile number formatting — compact, currency, percent, decimals.
18. Row-level bars in table tiles — cheapest upgrade to the plainest tile type.
19. Tile subtitle / description from the blob.
20. Total / average footer on tables, and sortable columns.

### D. Interactivity

21. **Drill down into chat.** Click a bar → new chat pre-seeded with that tile's query plus
    the clicked filter. The one thing that makes the board and the agent one product.
22. Click a legend entry to isolate a series.
23. Cross-tile hover sync across tiles sharing a time grain.
24. Per-tile overflow menu (`Menu.tsx` exists): view query, ask in chat, CSV, duplicate,
    resize, remove.
25. Expand a tile to full screen — the answer to "3 columns is too narrow".
26. A board-level filter bar — same shape as the date control, harder: inject where the cube
    carries the member, mark the rest unfiltered.
27. Brush / zoom on a time axis, client-side over rows already fetched.
28. Refresh feedback — a skeleton state and an elapsed timer; per-tile progress would lie.
29. Auto-refresh on an opt-in page timer.

### E. Board structure

30. Sections with headings inside `layouts.spec`.
31. Per-item height, not just span — a KPI row and a chart currently fight for the same box.
32. A dedicated KPI band across the top. `default_layout` already sorts KPIs first; the
    rendering does not.
33. Tabs / pages within one dashboard.
34. Responsive collapse — the grid is a hard `repeat(N)`.
35. Richer nav rows — headline sparkline, last-run status colour, chart-type icons.

### F. Looking better

36. Tile chrome: elevation, hover lift, a colour rail per chart type, a real spacing scale.
37. Chart fills — low-alpha `--series-*` under a line; bars use one of eight hues today.
38. Typography hierarchy — 25px KPI values sit too close to 13px titles.
39. Empty and loading states with shape — skeleton charts, not a sentence.
40. The insight paragraph as a card, with a headline and an "as of" chip.
41. Motion — fade-in, count-up, bars growing. Already gated by the `prefers-reduced-motion`
    block at the top of the stylesheet.
42. Board-level density toggle.

### G. Getting it out

43. **PDF export** (already on `things_to_do.md`). Print CSS is the cheap route — the charts
    are inline SVG and print natively.
44. PNG of a single tile, CSV per tile (a route over `tile_results`), presentation mode.

### H. Better insight

45. Anomaly flags connecting the prose to the tile it is about.
46. A headline sentence, separated and above the grid.
47. Insight history — `runs.insight` is kept per run and only the latest is ever shown.

---

## Order

1. **§A wholesale** — finishing work that is already three-quarters done.
2. **#13 big-number-with-trend, plus #36/#38 chrome and type scale** — what "looks like a
   dashboard" actually means to a viewer.
3. **#21 drill-down to chat** — the differentiating feature.
4. **#43 PDF** — already asked for, and print CSS is a day.

Pushed back on: **#8 donut** (rarely the right chart, and this codebase has been principled
about not shipping charts that mislead) and **#26 board filter bar** (per-tile member-existence
logic is a lot of subtlety for a second-order feature — do the date control first and learn
from it).

## Status

- [x] **A1 — drag to rearrange.** A grip in each tile header arms the cell for dragging (the
  tile itself is never draggable, or the chart loses its hover targets and the text stops
  selecting); arrow keys on the focused grip nudge a tile, so the feature is not mouse-only.
  A `n/N` button cycles a tile's span, a column picker sets the board width from
  `max_columns` (shipped by the route rather than mirrored in the browser), and Undo
  re-posts the previous spec. Each change is one new `layouts` version.
  Verified against a copy of the real DB through the actual route: reorder and span
  persist, and the two rejections the client guards against are real — a span wider than
  the board and a tile no longer on it both 400, which is why the client clamps spans on a
  column change and filters the item list to live tiles.
- [x] **§F — the visual pass.** Tile elevation and an accent hover border; a type scale
  (20px board title, 30px KPI figures, uppercase micro-labels on KPI labels and table
  headers); the window as a mono chip; delta pills tinted with `--ok`/`--bad` behind the
  arrow and sign that already carried direction; the insight as an accent-railed card with
  a Summary tag and the "as of" beside it; a shimmering skeleton for a never-refreshed tile
  so the grid does not reflow on first Refresh; a board-level indeterminate progress bar
  while refreshing (board-level, because the refresh is atomic); a fade-in on tiles; and a
  single-column collapse under 860px, which needs `!important` because the column count is
  an inline style off the saved layout.
  Area fill under a lone line series is **opt-in** (`ResultChart`'s `filled`) and passed
  only by `Tile` — chat charts are untouched, since they sit above their own table.
  Rendered and eyeballed in both themes via headless Chrome against the built CSS. That
  caught a real defect: `.dash-tile-table th` had no `text-align`, so every non-numeric
  column had a centred header over a left-aligned body. `.res-table` sets it; the tile
  table never did.
  Palette re-validated, unchanged: all six checks pass against `--raised` in both modes
  (worst adjacent CVD ΔE 9.7 protan light / 8.4 dark). Note it is `--raised` that carries
  it — against the page background three slots land at ~2.95:1, so a chart must never be
  put directly on `--bg`.
- [x] **A3 — per-tile captions.** One model call returns the board paragraph *and* a caption
  per tile — the rows are already in that prompt, so twelve calls buy nothing. The model
  returns a validated `BoardInsight` through `with_structured_output` (the modelgen pattern:
  the LLM returns decisions, never text to parse), and captions land on
  `tile_results.insight`, the column reserved for exactly this.
  - **Written after the rows**, in a second UPDATE, so the existing invariant holds: a model
    call that hangs costs captions and never the refresh's data.
  - **Filtered against the ids that went in.** A hallucinated id is dropped rather than
    written to nothing, and one bad entry does not take the rest with it.
  - **Omission is allowed and asked for.** A tile the model has nothing to say about is left
    out of the list — an obvious caption makes every caption after it not worth reading.
  - **Visibility is a `localStorage` toggle**, not board state: it is a display preference, so
    it belongs neither on the row (shared) nor in the hash (navigation). First use of
    localStorage in this frontend.
  Verified against the live model and container: structured output works on the query model,
  and a real refresh stored `"Rock holds a dominant share, with more tracks than Latin and
  Metal combined."` **A live run caught a prompt defect unit tests could not**: the first
  wording ("quoting its `tile_id` exactly as given") put the id into the prose, so every
  caption began *"Tile 23b47df95ece… shows…"*. The rule now says to set the field and never
  write the id or the word "tile" in the caption.
  Five new tests: captions land per tile, the window reaches both the compiled and the loaded
  payload, the window writes into the primary time member only, a tile with no time member is
  untouched, and clearing puts every tile back on its own range.
- [x] **#13 — the `stat` tile.** Latest bucket as the figure, its change from the bucket
  before, and the whole series as a sparkline, in one tile. Multiple measures render side by
  side, each in its own palette slot and directly labelled.
  - **The sparkline is the tile's own rows, not run history** — which is what kills A4's
    cold-start problem. One query gives the figure, the delta and the trend, so a stat tile
    is complete on its first refresh instead of after a fortnight of them.
  - **The delta is in-series** (last bucket vs the one before), so it means something
    different from `delta.py`'s run-to-run change. Its tooltip names the two buckets compared.
    Percent is null when the previous value is zero, matching `delta.py`.
  - **x must be a time grain.** Over a category "latest" is whichever row sorted last, shown
    as the current figure — rejected with a message pointing at bar instead.
  - **`chart_for` never picks it.** A chat result reading the shape of a series still gets the
    line it always had; `stat` only arrives from `set_chart` or a tile, so the transcript is
    unchanged. Both tool docstrings now name it.
  - **`layout.HEADLINE` is `("kpi", "stat")`,** so a stat leads a board the way a KPI does.
  Verified against live chinook: valid over a time grain, rejected over a category, a real
  refresh ran 60 monthly rows undegraded, and the board's default layout led with it. The
  measure arrives from Cube as the **string** `"35.64"`, which `numeric()` already handles.
  Rendered in both themes, which caught one defect: with `preserveAspectRatio="none"` a lone
  sparkline stretched to the full tile width and flattened its apparent slope by ~3.5×, so a
  trend's steepness depended on tile width. Capped at 300px.
- [x] **#5–#12 — the rest of the chart vocabulary.** Nine new types. The line family
  (`area`, `stacked_area`) and the bar family (`stacked_bar`, `grouped_bar`) extend the
  existing axes SVG; `hbar`, `donut`, `scatter`, `heatmap`, `gauge` and `sparkline` are
  standalone shapes in the same file.
  Decisions worth keeping:
  - **A plain `bar` still refuses a series** — it draws one and would silently ignore the
    split. `stacked_bar` and `grouped_bar` are the types that mean it.
  - **`area` is single-series only.** Two translucent areas over each other make a third
    colour that is not in the palette; the stacked variant is how you show several.
  - **The heatmap ramp is one hue at five opacities over the surface**, not a second set of
    tokens — monotonic in lightness by construction and correct in both themes for free.
  - **Segment gaps are 2px of surface**, on stacked bars and donut slices alike (the donut
    does it with a `--raised` stroke, the same trick `.res-dot` uses).
  - **`layout.HEADLINE` gained `gauge`; `FULL_WIDTH` gained `heatmap`.**
  - **`filled` is gone.** §F gave a tile's line chart an area fill; with `area` now a type of
    its own that was two ways to draw one picture, and it made `line` and `area` almost
    indistinguishable in a tile. A line is a line again.
  Five guard tests failed first and were meant to — they pin the type list, the rejection
  message and the serialised spec so the vocabulary cannot widen unnoticed. 561 pass.
  Verified by mounting every type against fabricated data in a throwaway Vite entry and
  screenshotting the live components, rather than hand-written markup — which tests the
  component and not just the CSS. That caught the gauge's 270° arc being clipped by a
  viewBox sized as if it were a half circle.
- [ ] A4 — KPI sparkline over history *(largely superseded by #13 — the only thing it still
  adds is a trend for a tile whose query returns a single row)*

### Known, not addressed

`ResultChart.tsx` is now ~800 lines and holds fourteen shapes. Splitting it is a real
restructure, so it is worth a decision rather than a drive-by.

### Deliberately not done in §F

- **#42 density toggle** — a control plus persisted state for a preference nobody has asked
  for twice. Skipped rather than deferred.
- **Colouring bars by category.** "More colours" is the ask, but a bar's category is already
  on the x-axis, so painting each bar differently is redundant encoding — it reads as a
  second variable that does not exist. Colour went where it carries meaning instead: status
  tints on deltas, the accent on the insight card and hover states, the fill under a line.
  A per-tile colour override belongs to #16, where a threshold gives it something to mean.
- **Chart entry animation** (bars growing, lines drawing). `transform-box: fill-box` on SVG
  paths for the baseline origin, for a flourish seen once per refresh.

### Noted while in there

`dashboards/layout.py::validate` is ~40 lines of hand-rolled `isinstance` / range / membership
checks — the exact thing a pydantic model replaces, and the pattern `cube.schema.CubeQuery`
already sets. Not touched, since nothing about A1 required it. Converting it would keep the
`(spec | None, errors)` return shape by turning `ValidationError` into the message list.
