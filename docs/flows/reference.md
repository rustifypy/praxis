# phase.yml reference

Both YAML files reject unknown fields. A misspelled field is an error that names itself, never a
setting that silently stops existing — `revieW: none` must not quietly remove a Gate.

## flow.yml

```yaml
title: Weekly Sales Digest
description: Week-over-week sales and revenue movements by region and category.
```

### `title`

Required. A string shown in the Flows list and at the top of every agent Phase's prompt.

### `description`

Optional, default empty. One line shown under the title, and given to every agent Phase.

There is deliberately no `phases:` list. The folders are the list, and two statements of the
order would be two things that can disagree.

## phase.yml

```yaml
kind: agent                         # or: script
review: required                    # or: none
tools: [query, python]              # agent only
inputs:
  - reference: _config/rules.md     # from the Flow folder, the same every run
  - working: brief.md               # from this run
  - working: 01_pull/output/stock.csv
outputs: [forecast.csv, notes.md]
may_be_empty: [notes.md]
queries: [stock.json]               # agent only, needs the query bundle
scripts: [forecast.py]              # agent only, needs the python bundle
```

### `kind`

Required. `agent` or `script`.

- **`agent`** — one model turn. The model gets the Phase's `CONTEXT.md` as instructions, its
  inputs, its prepared work, and the tools its bundles give it. It must write every declared
  output. Needs a non-empty `CONTEXT.md`.
- **`script`** — runs the one Python file named by [`script`](#script) in the sandbox. No model
  is called. It cannot adapt to a brief; put the judgement in an agent Phase before it.

### `review`

Optional, default `required`. `required` or `none`.

`required` puts a **Gate** after this Phase: the run stops, a person reads what landed in
`output/`, may edit it, and continues. `none` runs straight on to the next Phase. A Gate after the
last Phase has nothing to guard, so the run simply ends.

The default is `required` on purpose: a Flow whose author forgot the field pauses rather than
running past the person who wanted to read it.

### `tools`

Optional, default `[]`. A list of **bundles**, from a closed menu of two. `kind: agent` only.

| Bundle | Tools the Phase gets | Needs |
|---|---|---|
| `query` | `search_model`, `explain_last_query`, `extract`, and `extract_prepared` when the Phase has prepared queries | the semantic deployment |
| `python` | `run_python`, and `run_prepared` when the Phase has prepared scripts | the sandbox |

Every agent Phase also has `write_output`, whatever its bundles. A Phase with no bundles can only
read its inputs and write its outputs — which is right for a Phase that turns numbers into prose.

Ask for the least a Phase needs. A tidy-up Phase given warehouse access is how a tidy-up Phase
ends up re-running the analysis. There is no dashboards bundle: a Flow that silently rewrites a
board every Tuesday is a surprise nobody asked for.

### `inputs`

Optional, default `[]`. Each item names **exactly one** of `reference:` or `working:`.

**`reference: <path>`** resolves against the Flow's folder, e.g. `_config/voice.md` or
`phases/03_digest/references/example.md`. It must exist when the Flow is checked. It is pasted
into the prompt word for word, up to 20,000 characters (longer files are cut, and the prompt says
so). Put rules here: voice, format, thresholds, conventions.

**`working: <path>`** resolves against *this run's* folder. It is one of:

- `brief.md` — the text the run was started with;
- `NN_name/output/<file>` — a file an **earlier** Phase declared as an output.

A working input naming the same or a later Phase is an error. A working input is checked to
*exist* only when the Phase starts, because before that no run has made it.

How a working input reaches the model depends on its extension:

| Extension | The model sees | The sandbox has |
|---|---|---|
| `.csv` `.tsv` `.parquet` `.xlsx` `.xls` `.json` `.jsonl` | the file's shape: columns, types, distinct counts, nulls, ranges, a few rows | the whole file at `uploads/<filename>` |
| anything else (`.md`, `.txt`, …) | the whole text (up to 20,000 characters) | the whole file at `uploads/<filename>` |

### `outputs`

Optional, default `[]`, but a Phase with none gets a warning — it hands nothing on. Plain file
names. Each must exist in the Phase's `output/` when the Phase ends, or the Phase fails.

An output can be produced three ways:

1. `extract` / `extract_prepared` writing a CSV with a declared name;
2. a script (`run_python`, `run_prepared` or a `kind: script` file) writing the file by its plain
   name in the working directory, e.g. `df.to_csv("forecast.csv", index=False)`;
3. `write_output(filename, text)`, for prose the model writes.

A file that came from a query or a script always wins: `write_output` refuses a name that
`extract` or a script already wrote, so a model can never replace real data with a copy it typed.

### `may_be_empty`

Optional, default `[]`. Declared `.csv`/`.tsv` outputs for which a header with no rows is a
correct answer — a transfer plan when no store is short. Every name here must also be in
`outputs`.

Every other declared `.csv`/`.tsv` with only a header **fails the Phase**, because an empty file
the next Phase reads as "there is nothing" is usually a Phase that failed quietly.

### `script`

`kind: script` only, and required there. The file name of one `.py` beside `phase.yml`. It runs
in the sandbox with every working input at `uploads/<filename>`, and must write each declared
output by its plain name. A non-zero exit fails the Phase.

### `queries`

`kind: agent` only. Needs the `query` bundle. A list of `<name>.json` files in the Phase's
`queries/` folder, each a Cube JSON query. `extract_prepared("<name>.json")` runs one and writes
`<name>.csv`. See [Prepared work](prepared-work.md).

### `scripts`

`kind: agent` only. Needs the `python` bundle. A list of `.py` files beside `phase.yml`.
`run_prepared("<name>.py")` runs one exactly as written, **without asking for approval**. See
[Prepared work](prepared-work.md).

## Rules checked together

| Rule | Message you see |
|---|---|
| `kind: script` without `script:` | a script Phase needs 'script:' naming the .py file it runs |
| `kind: script` with `tools:` | a script Phase runs no model, so it cannot ask for tools |
| `kind: agent` with `script:` | 'script:' belongs to a script Phase — an agent Phase lists its prepared scripts under 'scripts:' |
| `kind: script` with `queries:`/`scripts:` | a script Phase runs its one 'script:' … |
| `queries:` without `query` in `tools:` | 'queries:' needs the query Bundle — add query to 'tools:' |
| `scripts:` without `python` in `tools:` | 'scripts:' needs the python Bundle — add python to 'tools:' |
| a query not `name.json`, a script not `name.py`, or either with a `/` | …: a query is a name.json in queries/, a script a name.py beside phase.yml |
| `may_be_empty` naming an undeclared file | may_be_empty names …, which 'outputs:' does not declare |
| an input with both or neither of `reference:`/`working:` | an input names exactly one of 'reference:' or 'working:' |
