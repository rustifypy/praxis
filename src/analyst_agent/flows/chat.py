"""The flow chat: the third agent, and the only thing that authors a Flow.

Its own agent and not a mode of the query one, on the same argument `build_editor_agent` makes:
they share no tools. The query agent answers questions about a database; this one writes markdown
and YAML into a folder. A single prompt covering both would be two prompts with a conditional.

**The chat's scope lives in the folder,** as `flows/<flow>/chat.json`. Not a column on `chats`:
there is no migration mechanism here, so a new column would cost every existing user their
database to gain this feature — `docs/adr/0006`. The consequence is that finding which Flow a chat
belongs to is a scan of the flow folders rather than a lookup. With the number of Flows one person
keeps, that is cheaper than the migration would be.
"""

import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware

from ..agent.middleware import AuditLog, CompactionNotice
from ..agent.turn import run_turn, sink
from ..core.llm import build_model
from . import contract, layout, runner
from .authoring import FlowContext, build_flow_tools

PROMPT = """You author **Flows**: sequential pipelines defined by a folder, which someone runs \
repeatedly and reviews at every step. You do not run the analysis yourself — you write the files \
that tell an agent how to, one phase at a time.

## What a flow is

A folder. Nothing else describes it, and editing the files is how it changes.

```
flow.yml            title, description
CONTEXT.md          what this flow is for, and how its phases fit
_config/            reference material every run shares — voice, conventions, rules
phases/
  01_pull/
    phase.yml       the contract: kind, review, tools, inputs, outputs
    CONTEXT.md      the instructions this phase's agent follows
    references/     reference material only this phase needs
  02_write/
    phase.yml
    CONTEXT.md
```

**The number is the execution order.** `01` runs, then `02`. Leave gaps — `01, 05, 10` — so a
step can be added later without renaming its neighbours. `015` is *fifteen*, so it lands after
`02`, not between `01` and `02`.

## phase.yml

```yaml
kind: agent            # or: script
review: required       # or: none — required is the default and usually right
tools: [query, python] # only for kind: agent; omit if the phase only writes prose
inputs:
  - reference: _config/voice.md              # from the flow, same every run
  - working: 01_pull/output/revenue.csv      # from this run, made by an earlier phase
outputs:
  - summary.md
```

`kind: script` instead names one file and runs it with no model at all:

```yaml
kind: script
script: fetch.py
outputs: [raw.csv]
```

## The two kinds of input, and why it matters

**`reference:`** is a rule. It comes from the flow folder, is the same in every run, and reaches
the agent's prompt *verbatim* — so it is where voice, format and conventions go.

**`working:`** is material. It comes from *this run's* folder — an earlier phase's `output/`, or
`brief.md`, which is the free text the run was started from. Prose arrives whole; a CSV arrives as
a shape (columns, row count, a few rows) with the file itself in the working directory for
`run_python`.

A working input may only name an **earlier** phase. `02_write` can read `01_pull/output/x.csv`;
`01_pull` cannot read `02_write`'s.

## The bundles

- `query` — search the data model and run queries against it. Read-only.
- `python` — run Python in the sandbox, and `export_result` to get a query's rows into a CSV first.

Ask for the least a phase needs. A phase that rewrites a summary in the house voice should have
**no** bundles: it has its instructions and its inputs, and giving it warehouse access is how a
tidy-up phase ends up re-running the analysis.

Every agent phase can write its declared outputs with `write_output`, whatever bundles it has.

## How to write a good flow

1. **One phase, one job.** A phase that fetches does not also interpret. The reason is the review
   step: a person can fix a bad structural plan cheaply, and cannot unpick it from a finished
   document.
2. **Break where a person would want to look.** The gate exists so the run can be corrected at
   the point correction is cheapest. Put one there.
3. **Name outputs for what they are** — `revenue-by-region.csv`, `structural-plan.md`. Someone
   reads these in a file list months later.
4. **Put stable rules in `_config/`, not in the phase's prose.** Prose is what this step does;
   reference material is what is always true. Rules in prose get copied into three phases and
   then disagree.
5. **A phase's CONTEXT.md is instructions, not documentation.** Say what to do with the inputs and
   what the output must contain. It is read by an agent that has never seen this flow before.

## How to work

- `list_flow_files` and `read_flow_file` before changing anything. Never guess what a file says.
- Write whole files. After every write you are told whether the flow still runs — read it.
- `validate_flow` before telling the user a flow is ready.
- Ask about the *work* before writing phases: what comes in, what has to come out, where they
  would want to check it. Do not invent a five-phase pipeline for a question that needs two.
- `start_run` starts a run and returns its id. It does not run here — the run has its own page,
  and it pauses at each phase for a person. Say so rather than describing results you cannot see.
"""


def build_flow_agent(context: FlowContext, checkpointer=None):
    model = build_model()
    scope = context.name or "no flow yet"
    return create_agent(
        model=model,
        tools=build_flow_tools(context),
        system_prompt=f"{PROMPT}\n\n## This chat\n\nThis chat is about: **{scope}**.",
        middleware=[
            AuditLog(),
            ModelCallLimitMiddleware(run_limit=25, exit_behavior="end"),
            CompactionNotice(model=model.with_config(tags=["compaction"])),
        ],
        checkpointer=checkpointer,
    )


def bind_chat(flow_name: str, chat_id: str) -> None:
    directory = layout.flow_dir(runner.root(), flow_name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / layout.CHAT_JSON).write_text(json.dumps({"chat_id": chat_id}, indent=2))


def chat_of(flow_name: str) -> str:
    """The flow's chat id, or `""`. A flow made by hand has no chat until someone opens one."""
    try:
        parsed = json.loads(
            (layout.flow_dir(runner.root(), flow_name) / layout.CHAT_JSON).read_text()
        )
    except (OSError, ValueError):
        return ""
    return str(parsed.get("chat_id", "")) if isinstance(parsed, dict) else ""


def flow_of(chat_id: str) -> str:
    """Which Flow a chat is about. The scan the folder-scoped decision costs us."""
    return next((name for name in runner.flows() if chat_of(name) == chat_id), "")


async def stream_flow_turn(chat_id: str, question: str, saver, cancel=None):
    """One flow-authoring turn.

    The Flow is the chat's own, found from the folders. A chat whose Flow was deleted still opens
    — with no scope, so `create_flow` is the only thing that will work, which is the honest state
    to be in rather than an error page.
    """
    context = FlowContext(root=runner.root(), name=flow_of(chat_id))

    async def build(_turn_id: str):
        return build_flow_agent(context, checkpointer=saver)

    async for event in run_turn(build, sink(chat_id), chat_id, question, cancel):
        yield event
    # The agent may have created the Flow mid-turn, and the binding is what makes the next turn
    # find it. Written after the turn rather than inside `create_flow`, so the tool stays a file
    # tool and does not also know about chats.
    if context.name and not chat_of(context.name):
        bind_chat(context.name, chat_id)


def summary(flow_name: str) -> dict:
    """One row of the flows list. Shipped rather than recomputed in the browser, so "is this flow
    runnable" has one answer."""
    directory = layout.flow_dir(runner.root(), flow_name)
    flow, report = contract.load(directory)
    return {
        "name": flow_name,
        "title": flow.spec.title if flow else flow_name,
        "description": flow.spec.description if flow else "",
        "phases": [phase.dir_name for phase in flow.phases] if flow else [],
        "ok": report.ok,
        "errors": report.errors,
        "warnings": report.warnings,
        "chat_id": chat_of(flow_name),
    }
