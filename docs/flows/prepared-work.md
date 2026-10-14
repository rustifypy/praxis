# Prepared work

A run whose agent has to find its data and write its script every time spends model calls and a
person's approval clicks on the same work — and does it slightly differently each run. So the
work is written **once, with the Flow**, and each run executes it.

```
phases/01_pull/
  phase.yml          tools: [query, python]
                     queries: [stock_position.json]
                     scripts: [shape_stock.py]
  queries/
    stock_position.json   writes stock_position.csv
  shape_stock.py          reads uploads/stock_position.csv, writes the declared outputs
```

## How a run uses it

The Phase's prompt includes each prepared query and script in full, under **"Prepared work — run
this"**, with the order to run them:

- `extract_prepared("stock_position.json")` runs that exact query and writes `stock_position.csv`.
- `run_prepared("shape_stock.py")` runs that exact script.

An **unchanged** prepared script runs without asking for approval: it was read when it was saved,
the same review a `kind: script` Phase gets. The match is on the whole text, so a model that edits
the script and sends it to `run_python` is asking to run a new script, and is asked to approve it.

The agent writes its own query or script only when the brief asks for something the prepared work
does not do, or a prepared step fails. It must say what it changed and why. The Run page marks any
step that was not the prepared one, so a reviewer sees the departure.

## Writing it so it works first time

1. **Queries first.** Find members with `search_model`, write `queries/<name>.json` with full
   member ids, and `check_query` it until it compiles. Leave `limit` out to get every row.
   `check_query` returns the exact CSV header `extract` will write. Use those names in scripts;
   never guess a column.
2. **Scripts second**, from those headers:
    - inputs and this Phase's extracts are at `uploads/<filename>`;
    - write each declared output by its plain name;
    - print a short summary — row counts, totals, anything odd — and never the rows.
3. **Write the Phase's `CONTEXT.md`** to say what the prepared work does, and what in a brief
   would need a different query or a changed script.
4. **Give the Phase `- working: brief.md`.** A Phase can only react to a brief it reads. A Phase
   with prepared work that does not read the brief gets a warning, because it will run the same
   thing whatever a run asks.
5. **Declare an output for every cut a brief may ask for**, with `may_be_empty` if it is
   optional. Outputs are fixed; an extra file the brief asked for that is not declared lands in
   `scratch/`, and the next Phase never sees it.
6. **Run `validate_flow`.** It checks every prepared query against the served model. A script
   cannot be checked until a run executes it.

## Dates and windows

!!! warning "Do not put a relative `dateRange` in a prepared query"
    Cube resolves `"last 14 days"` against **today's clock** every time the query runs. A
    prepared query runs on every run, months apart, while the data may stop days before today.
    The first Flow written this way held eight days of data in a "last 14 days" window, so "last
    week" was one day and the digest reported a 650% rise.

    Leave `dateRange` out (or use a wide fixed range like `["2026-01-01", "2026-12-31"]`) and cut
    the window in the script from the data's own latest date. `check_query` and `validate_flow`
    warn on a relative range.

Likewise `today`, thresholds and windows in a script come from the data or from `_config/`
rules, never from the machine clock or a hardcoded date.
