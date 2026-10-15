# Folder layout

## Where Flows live

Flows live in a `flows/` folder **in the directory you start `praxis` from**. Set `FLOWS_DIR` to
an absolute path to put them somewhere fixed. A folder under that root is a Flow when it holds a
`flow.yml`; anything else there is ignored.

A Flow's folder name is its identity. It must match `^[a-z0-9][a-z0-9-]*$` — lower case, digits
and hyphens — because it appears in URLs and in `ls`.

## The definition

```
flows/sales-digest/
  flow.yml                 title and description
  CONTEXT.md               what this Flow is for, and how its Phases fit
  _config/                 reference material every run shares
    voice.md
  phases/
    01_pull/
      phase.yml            the contract: kind, review, tools, inputs, outputs
      CONTEXT.md           the instructions this Phase's agent follows
      queries/             prepared Cube queries, one <name>.json each
        sales_by_store_daily.json
      references/          reference material only this Phase needs
    02_compute/
      phase.yml
      compute_wow.py       a prepared script, or the script of a kind: script Phase
    03_digest/
      phase.yml
      CONTEXT.md
```

| Path | Required | Read by |
|---|---|---|
| `flow.yml` | yes | the portal — see [flow.yml](reference.md#flowyml) |
| `CONTEXT.md` | recommended (a warning if empty) | every agent Phase's model, as "what this pipeline is for" |
| `_config/**` | no | a Phase that names a file there as a `reference:` input |
| `phases/NN_name/phase.yml` | yes | the portal — see [phase.yml](reference.md#phaseyml) |
| `phases/NN_name/CONTEXT.md` | yes for `kind: agent` | that Phase's model, as its instructions |
| `phases/NN_name/*.py` | for prepared scripts and script Phases | the sandbox |
| `phases/NN_name/queries/<name>.json` | for prepared queries | `extract_prepared` |
| `phases/NN_name/references/**` | no | a Phase that names it as a `reference:` input |

### Phase folder names

A Phase folder is named `NN_name`: two or more digits, an underscore, then lower-case letters,
digits and underscores (`^(\d{2,})_([a-z0-9][a-z0-9_]*)$`). A folder under `phases/` that does
not match is not a Phase and is skipped, so you can keep notes beside your Phases.

**The number is the execution order**, and it is read as a number, not as text:

- Number Phases `01`, `02`, `03` with no gaps, so a folder's name is its position.
- `10` correctly runs after `09`.
- `015` is *fifteen*. It runs after `02`, not between `01` and `02`.
- Two Phases with the same number is an error.

To **insert a step**, rename the folders after it (`02_compute` → `03_compute`) and update every
`working:` path that names a renamed folder. A path you miss fails that Phase at run time with
"input missing", because the check before a run can only see that the number is earlier.

### What can be written as part of the definition

The flow chat and the Edit tab may write only these paths, and are refused with a reason for
anything else:

- `flow.yml`, `CONTEXT.md`
- anything under `_config/`
- `phases/NN_name/phase.yml`, `phases/NN_name/CONTEXT.md`, `phases/NN_name/<file>.py`
- anything under `phases/NN_name/references/`
- `phases/NN_name/queries/<name>.json`

`runs/`, `chat.json` and `run.lock` are records of what ran and are never part of the definition.
A run's outputs can be edited, but only at a Gate, through the Runs page.

## A run

Every run gets its own folder under `runs/`, named by start time plus a short random suffix:

```
flows/sales-digest/
  runs/
    20261012T091500-3fa2c1/
      brief.md             the text the run was started with
      run.json             where the run is: status, current Phase, each Phase's state
      events.jsonl         everything that happened, one event per line, filed by Phase
      01_pull/
        output/            declared outputs — the next Phase can read these
        scratch/           anything else this Phase made — kept, but no later Phase sees it
      02_compute/
        output/
        scratch/
    latest -> 20261012T091500-3fa2c1
  chat.json                which chat authors this Flow
  run.lock                 held while a run is executing
```

- **`output/` vs `scratch/`**: a file a Phase declared in `outputs:` lands in `output/`.
  Anything else it wrote — a chart nobody declared, an intermediate CSV — lands in `scratch/`.
  Only `output/` is visible to later Phases.
- **`latest`** is a symlink for people in a terminal. Nothing in the portal reads it.
- **One run at a time per Flow.** `run.lock` is held while a run executes. Different Flows can
  run at the same time.
- The Phase list of a run is fixed when the run starts. Editing the Flow mid-run does not change
  a run that already exists.
