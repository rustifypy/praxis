# The flow chat

You do not have to write a Flow by hand. Open **Flows**, pick or create a Flow, and use the
**Edit** tab's chat. It is its own agent — separate from the question-answering chat — and its
only job is writing the Flow's folder. It does not run the analysis itself.

## What to tell it

Describe the *work*, not the files:

> Pull today's stock and the last 4 weeks of sales. Forecast demand. Then plan transfers from
> stores with too much stock to stores with too little. I want to review the forecast before
> transfers are planned.

It will ask what comes in, what has to come out, and where you want to check. It should not
invent a five-Phase pipeline for a question that needs two.

## What it can do

| Tool | What it does |
|---|---|
| `list_flows` | Every Flow under the root |
| `create_flow` | Make a new Flow folder with `flow.yml`, `CONTEXT.md`, `_config/` and an empty `phases/` |
| `list_flow_files`, `read_flow_file` | Look before changing anything |
| `write_flow_file`, `delete_flow_file` | Write one whole file. After every write it is told whether the Flow still passes the checks |
| `search_model` | Find measures and dimensions in the served model — reads the model, never data |
| `check_query` | Check a Cube query exactly as a run will, without running it, and return the CSV header `extract` will write |
| `validate_flow` | Every write-time check, plus every prepared query compiled against the model |
| `start_run` | Start a run from a brief and return its id. The run happens on the Runs tab, not in the chat |
| `read_run_state` | Where the most recent run got to, and which Phases are stale |

It writes only the paths listed in [What can be written](layout.md#what-can-be-written-as-part-of-the-definition).

## What it is told

The flow chat's instructions cover: consecutive numbering; `may_be_empty`; bundles and asking for
the least; prepared queries first, checked, then scripts from their headers; giving Phases with
prepared work the brief; no relative `dateRange`; and `validate_flow` before calling a Flow
ready. It ends by telling you that scripts are checked only when a run executes them — the first
run of a new Flow is its test.

## Editing by hand

Every file is also editable in the Edit tab's file list, grouped by Phase. Saves are checked the
same way. Because the folder is the Flow, you can equally edit it in your own editor and commit
it to git. Note that the repository's `.gitignore` ignores `/flows/*`, so keep Flows you want
versioned in their own repository, or point `FLOWS_DIR` at one.

The question-answering chat **cannot** start a run; see
[decision 0007](../adr/0007-the-analytics-agent-cannot-run-a-flow.md).
