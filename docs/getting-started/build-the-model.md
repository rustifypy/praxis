# 4. Build the data model

Step 4 of 5 — setup step 4, **The data model**. At the end of it, Cube is serving a model of
the tables you picked, and the portal opens on **Chat**.
{ .ax-step }

## What you are building

A **semantic layer** is a description of your data that a computer can check: which tables
matter, what each column means, how tables join, which numbers can be added up (**measures**) and
which values you group and filter by (**dimensions**). Every question is answered through it, so
the agent can only use joins and formulas the model defines.

## Pick the tables


<div class="ui">
--8<-- "docs/.snippets/ui-setup.html"
<div class="ui-main">
<div class="ui-picker">
<div class="ui-picker-head"><span class="ui-faint">Schema</span><span class="ui-input">public ▾</span><span class="ui-input empty" style="flex:1;min-width:120px">Filter 7 tables</span><span class="ui-btn">All</span><span class="ui-btn">None</span><span class="ui-btn">Facts</span><span class="ui-btn link">Re-read</span></div>
<div>
<div class="ui-pick"><span class="ui-box on"></span><span class="ui-pick-name">fact_daily_sales<span class="ui-tag">fact</span></span><span class="ui-pick-meta">9 cols · → dim_stores, dim_items</span></div>
<div class="ui-pick"><span class="ui-box soft"></span><span class="ui-pick-name">dim_stores<span class="ui-tag accent">referenced</span></span><span class="ui-pick-meta">6 cols</span></div>
<div class="ui-pick"><span class="ui-box soft"></span><span class="ui-pick-name">dim_items<span class="ui-tag accent">referenced</span></span><span class="ui-pick-meta">7 cols</span></div>
<div class="ui-pick"><span class="ui-box"></span><span class="ui-pick-name">fact_stock_daily<span class="ui-tag">fact</span></span><span class="ui-pick-meta">6 cols · → dim_stores, dim_items</span></div>
<div class="ui-pick"><span class="ui-box"></span><span class="ui-pick-name">fact_transfers<span class="ui-tag">fact</span></span><span class="ui-pick-meta">8 cols · → dim_stores, dim_items</span></div>
<div class="ui-pick"><span class="ui-box"></span><span class="ui-pick-name">dim_calendar</span><span class="ui-pick-meta">5 cols</span></div>
<div class="ui-pick"><span class="ui-box"></span><span class="ui-pick-name">staging_import<span class="ui-tag warn">no key</span></span><span class="ui-pick-meta">12 cols</span></div>
</div>
<div class="ui-picker-foot"><span><strong>3 of 7 tables</strong> <span class="ui-muted">— including dim_stores, dim_items because your selection references them</span></span><span class="ui-row"><span class="ui-row ui-muted"><span class="ui-box on"></span>Also write descriptions and views with AI</span><span class="ui-btn primary">Build the model from 3 tables</span></span></div>
</div>
</div>
</div>
<p class="ui-caption">One fact table ticked; the two dimension tables it joins to come with it.</p>

- **Schema** selects which schema to list. **Filter** narrows the list.
- **All**, **None** and **Facts** select quickly. *Facts* are tables nothing points at — where
  additive numbers usually live.
- Tables your selection points at through foreign keys are ticked for you: *Included because your
  selection references it*.
- The footer warns about joins that will be dropped, and tables with no key that nothing can join
  to.
- **Re-read** picks up a schema change in the warehouse.

Tick **Also write descriptions and views with AI** to run all four steps. Without it the model
is cubes only — no descriptions and no views — and the agent has less to go on.

Click **Build the model from N tables**. A progress panel shows each step, the table it is on,
and a log. **Cancel** stops it.

If **Describe** or **Views** fails, the cubes are already built and serving. You can carry on and
ask the model editor's assistant for descriptions and views instead.

## What the build does

| Step | What happens |
|---|---|
| **Read the database** | Reads every schema and table, with keys and foreign keys. Runs on its own when you arrive. Nothing is written to the model |
| **Build cubes** | Each table you picked becomes a **cube**, with its keys and joins |
| **Describe with AI** | The AI writes a description of each table and column, and suggests measures. One model call per table — the slow part |
| **Build views** | Builds **views**: clean, joined surfaces for questions, grouped into folders |

The AI returns **decisions**, never files. The portal writes the files, and **Cube is the
judge**: each build goes to a temporary branch first, and only if Cube compiles it does it
replace what is served. If Cube refuses, the message says what failed and *nothing was replaced*.

## The words

| Word | Means | Example |
|---|---|---|
| **Cube** | One business thing, from one table | `fact_daily_sales` |
| **Measure** | A number you can add up or count | `total_revenue` |
| **Dimension** | A value you group or filter by | `region`, `sale_date` |
| **Join** | How two cubes relate | a sale belongs to a store |
| **View** | A joined surface the agent asks questions against | `daily_sales` = sales + stores + items |
| **Folder** | A group of members inside a view | "Store", "Money" |
| **Segment** | A named, reusable filter | `orders.paid_only` |

When the build finishes, the portal opens on **Chat**.

**Next:** [5. Ask your first question](first-question.md)
