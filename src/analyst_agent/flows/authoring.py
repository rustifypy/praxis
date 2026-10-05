"""The flow chat's toolset: writing a Flow's files, and starting a Run.

**This is the one toolset not in `agent/tools/`,** and the reason is a tier. Every tool here has
to reach `contract` to say whether what it just wrote is a Flow that can run, and `contract` lives
in this package — above `agent/`. Moving it down would split `flows/` in two so that a tier-2 tool
could name half of it, which is the arrangement `docs/adr/0007` rejected. So the exception is
deliberate and this docstring is where it is written down.

The tools are file tools, not builders. There is no `create_phase(name, kind, tools)`: a Flow is
its folder, and a structured tool would become the real interface with the files as its output —
the two would drift the first time somebody hand-edited one. What keeps that honest is that every
write is validated the same way a route's write is, and the agent is told what is wrong in the
same words.

Two tools read the data model, never data: `search_model` and `check_query`. They exist for a
Phase's prepared queries — written when the Flow is, so a Run does not search for its data each
time — and `check_query` returns the CSV header an extract will write, which is what lets a
prepared script be written against real column names rather than guessed ones.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from langchain.tools import tool

from ..agent.retrieval import LexicalRetriever, render
from ..agent.tools.trace import end, start
from ..core.config import settings
from ..modelstore import gate
from ..modelstore.client import StoreError, open_store
from ..modelstore.shape import columns_of
from . import contract, layout, runner, state


@dataclass
class FlowContext:
    """Which Flow this chat is about, and where flows live.

    Scoped to one Flow for the reason an edit chat is scoped to one ref: a transcript that
    followed the page's selection would describe edits to a Flow it was not editing. `name` is
    empty only until the chat creates one — a conversation can legitimately start with "make me a
    flow that…" and have nothing to point at yet.
    """

    root: Path
    name: str = ""

    @property
    def directory(self) -> Path:
        return layout.flow_dir(self.root, self.name)


def scaffold(root: Path, name: str, title: str, description: str = "") -> Path:
    """The folders and two files a new Flow starts as.

    One function for the tool and the route both, because what a new Flow *is* is one rule. The
    two spellings of it drifted within an hour of existing — the route's version wrote a different
    `CONTEXT.md` opening line than the agent's did.

    No phases: a Flow with an invented `01_something` would be a guess at the work, and the whole
    point of the authoring conversation is to ask instead.
    """
    directory = layout.flow_dir(root, name)
    (directory / layout.PHASES).mkdir(parents=True)
    (directory / layout.CONFIG).mkdir(exist_ok=True)
    (directory / layout.FLOW_YML).write_text(
        f"title: {title}\n" + (f"description: {description}\n" if description else "")
    )
    opening = description or "What this flow is for, and how its phases fit."
    (directory / layout.CONTEXT_MD).write_text(f"# {title}\n\n{opening}\n")
    return directory


def build_flow_tools(context: FlowContext) -> list:
    def said(report: contract.Report) -> str:
        if report.ok and not report.warnings:
            return "The flow is valid."
        lines = [f"- {line}" for line in report.errors]
        lines += [f"- (warning) {line}" for line in report.warnings]
        head = "The flow is valid, with warnings:" if report.ok else "The flow will not run:"
        return "\n".join([head, *lines])

    def scoped() -> str:
        return "" if context.name else "This chat has no flow yet. Call `create_flow` first."

    @tool
    def list_flows() -> str:
        """Every flow on this machine, and which one this chat is about."""
        start("list_flows", "")
        names = runner.flows()
        end("list_flows", True, note=f"{len(names)} flows")
        if not names:
            return "There are no flows yet. `create_flow` makes one."
        marked = [
            f"- {name}" + ("  <- this chat" if name == context.name else "") for name in names
        ]
        return "\n".join(marked)

    @tool
    def read_flow_file(path: str) -> str:
        """Read one file of this flow. Paths are relative to the flow folder, like
        `phases/01_pull/phase.yml` or `_config/voice.md`."""
        start("read_flow_file", path)
        if refusal := scoped():
            end("read_flow_file", False, errors=[refusal])
            return refusal
        if refusal := layout.writable(path):
            end("read_flow_file", False, errors=[refusal])
            return refusal
        target = context.directory / path
        if not target.is_file():
            end("read_flow_file", False, errors=["not there"])
            return f"{path} is not there. `list_flow_files` shows what is."
        text = target.read_text(errors="replace")
        end("read_flow_file", True, note=f"{len(text)} characters")
        return text

    @tool
    def list_flow_files() -> str:
        """Every file this flow's definition is made of, in path order."""
        start("list_flow_files", context.name)
        if refusal := scoped():
            end("list_flow_files", False, errors=[refusal])
            return refusal
        found = sorted(
            str(p.relative_to(context.directory))
            for p in context.directory.rglob("*")
            if p.is_file() and not layout.writable(str(p.relative_to(context.directory)))
        )
        end("list_flow_files", True, note=f"{len(found)} files")
        return "\n".join(f"- {path}" for path in found) or "(this flow has no files yet)"

    @tool
    def write_flow_file(path: str, text: str) -> str:
        """Write one file of this flow, creating it if it is not there.

        The whole file, every time — there is no patching. After the write the flow is checked and
        the result comes back, so a `phase.yml` with a bad field says so immediately.
        """
        start("write_flow_file", path)
        if refusal := scoped():
            end("write_flow_file", False, errors=[refusal])
            return refusal
        if refusal := layout.writable(path):
            end("write_flow_file", False, errors=[refusal])
            return refusal
        target = context.directory / path
        # Checked even though `writable` refused `..` already: the string rule and the resolved
        # path are two different questions, and a symlink inside the folder answers them
        # differently.
        if not layout.contained(context.directory, target):
            end("write_flow_file", False, errors=["outside the flow"])
            return f"{path} resolves outside the flow folder."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        _, report = contract.load(context.directory)
        end("write_flow_file", report.ok, note=f"{path}, {len(text)} characters")
        return f"Wrote {path}.\n\n{said(report)}"

    @tool
    def delete_flow_file(path: str) -> str:
        """Delete one file of this flow. Removing a phase means deleting its files."""
        start("delete_flow_file", path)
        if refusal := scoped():
            end("delete_flow_file", False, errors=[refusal])
            return refusal
        if refusal := layout.writable(path):
            end("delete_flow_file", False, errors=[refusal])
            return refusal
        target = context.directory / path
        if not target.is_file():
            end("delete_flow_file", False, errors=["not there"])
            return f"{path} is not there, so nothing was deleted."
        target.unlink()
        # The folder goes too when it empties, because an empty `NN_name/` is a phase with no
        # contract and `contract.load` would refuse the whole flow for it.
        if not any(target.parent.iterdir()):
            target.parent.rmdir()
        _, report = contract.load(context.directory)
        end("delete_flow_file", True, note=path)
        return f"Deleted {path}.\n\n{said(report)}"

    @tool
    def create_flow(name: str, title: str, description: str = "") -> str:
        """Make a new flow folder and point this chat at it.

        `name` is the folder's name and the flow's identity: lower case, digits and hyphens.
        """
        start("create_flow", name)
        if not layout.FLOW_NAME.match(name):
            end("create_flow", False, errors=["bad name"])
            return f"{name!r} cannot be a flow name — lower case letters, digits and hyphens only."
        directory = layout.flow_dir(context.root, name)
        if directory.exists():
            end("create_flow", False, errors=["exists"])
            return f"{name} already exists. Read it with `list_flow_files` before changing it."
        scaffold(context.root, name, title, description)
        context.name = name
        end("create_flow", True, note=name)
        return (
            f"Created {name}. It has no phases yet, so it cannot run — add "
            f"`{layout.PHASES}/01_<name>/{layout.PHASE_YML}` and its {layout.CONTEXT_MD}."
        )

    @tool
    async def search_model(question: str) -> str:
        """Find the measures and dimensions in the data model, to write a prepared query with.
        Returns member ids, types and valid filter operators. It reads the model, never data."""
        start("search_model", question)
        try:
            async with await open_store() as store:
                hits = LexicalRetriever(await store.at().catalog()).search(question)
        except (StoreError, httpx.RequestError) as exc:
            end("search_model", False, errors=[str(exc)])
            return f"The data model could not be read: {exc}"
        end("search_model", True, hits=len(hits))
        return render(hits) if hits else "The data model is empty."

    @tool
    async def check_query(query: dict) -> str:
        """Check a Cube query exactly as a run will, without running it: its shape, its members
        and operators, and Cube compiling it. Returns the columns `extract` will write, in order —
        the exact CSV header a prepared script reads. Call it on every prepared query."""
        start("check_query", "")
        try:
            async with await open_store() as store:
                bound = store.at()
                catalog = await bound.catalog()
                checked = await gate.check(query, catalog, bound, settings.flow_page_rows)
        except (StoreError, httpx.RequestError) as exc:
            end("check_query", False, errors=[str(exc)])
            return f"The query could not be checked — the data model did not answer: {exc}"
        if not checked.ok:
            end("check_query", False, errors=checked.errors)
            return checked.as_message() + "\n\nFix the query and check it again."
        end("check_query", True)
        header = "\n".join(f"- {c.id} ({c.type or c.kind})" for c in columns_of(query, catalog))
        warned = f"\n\n{checked.as_message()}" if checked.warnings else ""
        return f"It compiles. `extract` writes these columns, in this order:\n{header}{warned}"

    @tool
    async def validate_flow() -> str:
        """Check this flow without running it: fields, phase numbering, every declared input that
        can be checked before a run exists, and every prepared query against the data model."""
        start("validate_flow", context.name)
        if refusal := scoped():
            end("validate_flow", False, errors=[refusal])
            return refusal
        flow, report = contract.load(context.directory)
        # The half `contract.load` cannot do: whether a prepared query's members exist and Cube
        # compiles it. It needs the served model, so it is asked here and not on every write.
        if flow is not None and report.ok and any(p.spec.queries for p in flow.phases):
            try:
                async with await open_store() as store:
                    bound = store.at()
                    catalog = await bound.catalog()
                    for phase in flow.phases:
                        for name in phase.spec.queries:
                            text = (phase.directory / layout.QUERIES / name).read_text()
                            checked = await gate.check(
                                json.loads(text), catalog, bound, settings.flow_page_rows
                            )
                            report.errors += [
                                f"{phase.dir_name}: query {name} — {error}"
                                for error in checked.errors
                            ]
            except (StoreError, httpx.RequestError) as exc:
                report.warnings.append(f"prepared queries were not checked — {exc}")
            report.ok = not report.errors
        end("validate_flow", report.ok, errors=report.errors)
        return said(report)

    @tool
    def start_run(brief: str) -> str:
        """Start a run of this flow from a brief, and return its id.

        The run does not happen in this conversation — it has its own page and its own log, and it
        pauses at each phase for someone to read what landed. This only starts it.
        """
        start("start_run", brief[:60])
        if refusal := scoped():
            end("start_run", False, errors=[refusal])
            return refusal
        run, report = runner.begin(context.name, brief)
        if run is None:
            end("start_run", False, errors=report.errors)
            return f"Nothing was started.\n\n{said(report)}"
        end("start_run", True, note=run.id)
        return (
            f"Started run {run.id} of {context.name}, waiting at {run.current}. "
            "It runs on the flow's Runs tab — open it there to step through the phases."
        )

    @tool
    def read_run_state() -> str:
        """Where this flow's most recent run got to, and which phases are stale."""
        start("read_run_state", context.name)
        if refusal := scoped():
            end("read_run_state", False, errors=[refusal])
            return refusal
        # `layout.run_ids` and not a listing of its own: `runs/latest` is a symlink to a real Run
        # directory, so anything counting `is_dir()` folders finds the newest Run twice.
        found = [
            state.read(layout.run_dir(context.directory, run_id))
            for run_id in layout.run_ids(context.directory)
        ]
        current = next((run for run in found if run is not None), None)
        if current is None:
            end("read_run_state", False, errors=["no runs"])
            return f"{context.name} has not been run yet."
        lines = [
            f"Run {current.id}: {current.status}"
            + (f", waiting on {current.waiting}" if current.waiting else "")
        ]
        lines += [
            f"- {p.dir_name}: {p.status}" + (f" — {p.error}" if p.error else "")
            for p in current.phases
        ]
        end("read_run_state", True, note=current.status)
        return "\n".join(lines)

    return [
        list_flows,
        create_flow,
        list_flow_files,
        read_flow_file,
        write_flow_file,
        delete_flow_file,
        search_model,
        check_query,
        validate_flow,
        start_run,
        read_run_state,
    ]
