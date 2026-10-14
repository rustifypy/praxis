# Run a Flow

A **Flow** is data work you repeat, done in numbered **Phases** with a person checking the
result before it goes on. To write one, see [Flow authoring](../flows/index.md).

Open **Flows**, pick a Flow, and open the **Runs** tab. A Flow marked *will not run* has a
problem; the **Edit** tab says what.

## Start a run

Write a **brief** — what this run is for — and click **Start a run**:

> Focus on the North region, and say which stores drove the change.

The brief is saved with the run as `brief.md`. Phases that read it can change what they pull or
write; Phases that do not, run the same way every time.

A new run **waits** at the first Phase. Nothing executes until you press **Run 01_pull**.

## The run story

<div class="ui">
--8<-- "docs/.snippets/ui-flows.html"
<aside class="ui-side">
<div class="ui-side-head"><span>Flows</span></div>
<div class="ui-item on">Weekly Sales Digest<small>3 phases</small></div>
<div class="ui-item">Stock transfers<small>4 phases</small></div>
<div class="ui-group">Runs</div>
<div class="ui-item on">Oct 14, 09:30<small>paused · 2 of 3</small></div>
<div class="ui-item">Oct 7, 09:28<small>done</small></div>
</aside>
<div class="ui-main">
<div class="ui-spread"><span><span class="ui-h2">Run · Oct 14, 09:30</span> <span class="ui-tag warn">paused</span> <span class="ui-faint">2 of 3 phases done</span></span><span class="ui-btn primary">Continue to 03_digest</span></div>
<p class="ui-faint"><b>Brief</b> — Which North stores drove the change?</p>
<div class="ui-phase">
<div class="ui-phase-head"><span class="ui-mark">✓</span><b>01 · Pull</b><span class="ui-faint">An agent that can query the data model.</span></div>
<div class="ui-label">What it did</div>
<ul class="ui-did"><li>Pulled total units sold and total revenue by date, region and category into sales_by_region_category_daily.csv · 392 rows</li><li>Pulled total revenue by date and store into sales_by_store_daily.csv · 812 rows</li></ul>
<div class="ui-label">What it produced</div>
<div class="ui-file"><span>sales_by_region_category_daily.csv</span><span class="ui-faint">392 rows</span></div>
<div class="ui-file"><span>sales_by_store_daily.csv <span class="ui-tag">may be empty</span></span><span class="ui-faint">812 rows</span></div>
</div>
<div class="ui-phase current">
<div class="ui-phase-head"><span class="ui-mark">✓</span><b>02 · Compute</b><span class="ui-faint">A fixed script, no model.</span></div>
<div class="ui-label">What it did</div>
<ul class="ui-did"><li>Ran compute_wow.py</li></ul>
<div class="ui-label">What it produced</div>
<div class="ui-file"><span>wow_by_region.csv</span><span class="ui-faint">4 rows</span></div>
<div class="ui-file"><span>wow_by_store.csv</span><span class="ui-faint">58 rows</span></div>
<div class="ui-file"><span>movers.csv</span><span class="ui-faint">10 rows</span></div>
<div class="ui-gate"><span>⏸ Paused here for your review.</span><span>Read what Compute produced above, and edit its outputs if something is wrong.</span></div>
</div>
<div class="ui-phase" style="opacity:.6">
<div class="ui-phase-head"><span class="ui-mark" style="color:var(--faint)">3</span><b>03 · Digest</b><span class="ui-faint">An agent that writes from what it reads.</span></div>
<div class="ui-faint">Reads _config/voice.md, brief.md and four files from 02_compute.</div>
</div>
</div>
</div>
</div>
<p class="ui-caption">A run paused at a review point after its second Phase. The next Phase reads the outputs exactly as they are when you press <b>Continue</b>.</p>


The run page tells the story of the run, Phase by Phase.

- The header shows status, *X of N phases done*, and the main action: **Run** *phase*,
  **Continue to** *phase*, or **Stop** while it works.
- A sentence says where things are: *Waiting for you. 02_forecast is done and paused for review;
  03_plan runs when you continue.*
- Each Phase section shows:
    - **Reads** — the brief, files from earlier Phases;
    - **Follows** — the rule files it obeys;
    - **How** — *a fixed script, no model*, *an agent that can query the data model and run
      Python*, or *an agent that writes from what it reads*;
    - **Prepared** — the queries and scripts written with the Flow;
    - **What it did** — plain sentences: *Pulled stock by store into stock_position.csv · 348
      rows*, *Ran the prepared script forecast.py*. A step marked **not the prepared one** is
      the agent doing something different from the prepared work — read why in its output;
    - **What it produced** — each output, with its row count and a preview. *Also saved, but not
      passed on* lists files in `scratch/`.

The page re-reads the run every two seconds while it works.

## Review points

A Phase with `review: required` ends at a **review point**. The run stops: *Paused here for your
review.*

1. Read what the Phase produced.
2. If something is wrong, click **Edit** on an output, fix it, and **Save — the next phase reads
   this**. Only the Phase just before the review point can be edited.
3. Click **Continue to <next phase>**.

A review point is written to disk, so it survives a portal restart.

## Script approval

An agent Phase that writes its **own** script asks first: *Wants to run a script. Read it, then
decide.* — **Run**, **Always in this chat**, or **Decline**. A prepared script that is unchanged
runs without asking. An approval waiting when the portal restarts fails the Phase.

## When something goes wrong

A failure stops the run where it is: *Stopped at 02_forecast: declared output has no rows:
forecast.csv. Fix the cause, then use "Re-run from here" on that phase.* See
[What fails a Phase](../flows/failures.md) for every message.

- **Re-run from here** points the run back at that Phase. Phases after it that had finished become
  **stale** — still readable, but they may no longer follow from what came before. Press **Run**
  to go on.
- **Stop** ends the Phase now, as a failure: *stopped by the user*.
- **Delete run** (type `delete`) removes the run's folder. A run that is going must be stopped
  first.

Every run is kept as files under the Flow's `runs/` folder: the brief, each Phase's outputs, and a
log of every step.
