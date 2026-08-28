"""One Run: the loop that executes Phases, pauses at **Gates**, and fails loudly.

`advance` is the only entry point a route needs, and calling it repeatedly is how a Run proceeds.
It runs Phases until one of four things happens: a Gate is reached, a Phase fails, the Run ends,
or the caller cancels. It is not "run one Phase" — a Phase with `review: none` was written to be
run *through*, and stopping after it anyway would make the Gate a suggestion.

**Everything that goes wrong fails the Phase.** A spent model-call limit, an unanswered approval,
a declared output that never appeared: each stops the Run where it is and says why. This is not
defensiveness, it is the whole difference between an unattended Run and a chat. A chat that runs
out of model calls writes the best answer it has and a person reads it in context. A Run that did
the same would leave a confident file in `output/` that the next Phase would treat as finished
work — so `exit_behavior` here is `error`, not `end`.

The Phase list comes from `run.json`, fixed when the Run began, and not from the folders as they
are now. A Flow edited mid-Run must not change what this Run is.
"""

import asyncio
import contextlib
import fcntl
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware

from ..agent.middleware import AuditLog
from ..agent.session import Session
from ..agent.turn import Sink, run_turn
from ..core.config import settings
from ..core.events import Event
from ..core.llm import build_model
from ..modelstore.client import open_store
from ..sandbox import SandboxError, open_sandbox
from ..sandbox import profile as profiles
from . import contract, events, layout, prompt, state, tools

# Tighter than a chat's 40, and it raises rather than ending. A Phase is a scoped task: needing
# twenty model calls to write one document is a sign the Phase is doing two Phases' work.
PHASE_MODEL_CALLS = 20
PHASE_PYTHON_CALLS = 8

# What counts as data rather than prose, and so arrives as a shape instead of contents. By
# extension and not by sniffing: crude, but a wrong guess here costs a re-run, while sniffing
# wrongly on a 200MB file costs the Phase.
TABULAR = (".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".json", ".jsonl")


class Busy(RuntimeError):
    """Another Run of this Flow holds the lock."""


@contextlib.contextmanager
def hold(flow: Path):
    """One Run at a time per Flow.

    Deliberately not `core.jobs.hold`, whose lock is machine-wide: a twelve-minute Run must not
    block a model rebuild, and a model rebuild must not block every Flow. Different Flows run
    concurrently — they share nothing but this process.
    """
    flow.mkdir(parents=True, exist_ok=True)
    handle = (flow / layout.LOCK).open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            raise Busy(handle.read().strip() or "another run") from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid {os.getpid()}")
        handle.flush()
        yield
    finally:
        handle.close()


def root() -> Path:
    return settings.flows_path


def flows() -> list[str]:
    """Every Flow folder, by name. A folder without a `flow.yml` is not one — the root is a
    directory someone else also keeps things in."""
    here = root()
    if not here.is_dir():
        return []
    found = [p.name for p in here.iterdir() if (p / layout.FLOW_YML).is_file()]
    return sorted(found)


def begin(flow_name: str, brief: str) -> tuple[state.RunState | None, contract.Report]:
    """Create a Run: its folder, its **Brief**, and `run.json` at Phase one.

    The Flow is validated first and a broken one never gets a Run folder. A half-created Run that
    exists only to say the Flow was wrong would be one more thing in the Runs list to explain.
    """
    directory = layout.flow_dir(root(), flow_name)
    flow, report = contract.load(directory)
    if flow is None or not report.ok:
        return None, report

    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run = layout.run_dir(directory, run_id)
    run.mkdir(parents=True)
    # The Brief is a Working input like any other, so it is a file in the Run before anything
    # runs — and a permanent record of what this Run was actually asked for.
    (run / layout.BRIEF_MD).write_text(brief)
    fresh = state.begin(run_id, flow_name, [phase.dir_name for phase in flow.phases])
    state.write(run, fresh)
    _point_at_latest(directory, run_id)
    return fresh, report


def _point_at_latest(directory: Path, run_id: str) -> None:
    # A convenience for whoever is in a terminal, and nothing reads it: `runs/latest` is a
    # symlink, so a filesystem that will not make one costs a shortcut and not a Run.
    link = layout.runs_dir(directory) / layout.LATEST
    with contextlib.suppress(OSError, NotImplementedError):
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(run_id)


async def advance(
    flow_name: str,
    run_id: str,
    cancel: asyncio.Event | None = None,
    approvals: asyncio.Queue | None = None,
) -> AsyncIterator[Event]:
    """Run Phases until a Gate, a failure, or the end of the Flow.

    Holds the Flow's lock throughout, so a second caller gets `Busy` rather than two Runs writing
    one `runs/latest`. Every event is both yielded and appended to the Run's log: the log is what
    someone watching a Run reads, and the yield is what the stream they are watching carries.
    """
    directory = layout.flow_dir(root(), flow_name)
    run = layout.run_dir(directory, run_id)
    with hold(directory):
        flow, report = contract.load(directory)
        current = state.read(run)
        if flow is None or current is None:
            yield Event(type="error", data={"message": "; ".join(report.errors) or "no such run"})
            return
        # Re-checked here and not only at authoring time: the folder may have been edited by hand
        # since, and a Run is the expensive place to discover it.
        if not report.ok:
            current.status, current.error = "failed", "; ".join(report.errors)
            state.write(run, current)
            yield Event(type="error", data={"message": current.error})
            return

        append = events.sink(run)
        while True:
            phase = next((p for p in flow.phases if p.dir_name == current.current), None)
            if phase is None:
                current.status, current.current, current.ended_at = "done", "", time.time()
                state.write(run, current)
                yield Event(type="done", data={"run_id": run_id, "status": "done"})
                return

            async for event in _one_phase(flow, phase, run, current, append, cancel, approvals):
                yield event

            record = current.phase(phase.dir_name)
            if record is None or record.status == "failed":
                current.status, current.ended_at = "failed", time.time()
                current.error = (record and record.error) or "the phase failed"
                state.write(run, current)
                yield Event(type="done", data={"run_id": run_id, "status": "failed"})
                return

            following = _after(flow, phase.dir_name)
            current.current = following
            if phase.spec.review == "required" and following:
                current.status, current.waiting = "paused", "gate"
                state.write(run, current)
                yield Event(
                    type="done", data={"run_id": run_id, "status": "paused", "gate": following}
                )
                return
            current.status, current.waiting = "running", ""
            state.write(run, current)
            if cancel is not None and cancel.is_set():
                yield Event(type="done", data={"run_id": run_id, "status": "paused"})
                return


def _after(flow: contract.Flow, dir_name: str) -> str:
    names = [p.dir_name for p in flow.phases]
    following = names.index(dir_name) + 1
    return names[following] if following < len(names) else ""


async def _one_phase(
    flow: contract.Flow,
    phase: contract.Phase,
    run: Path,
    current: state.RunState,
    append: Sink,
    cancel: asyncio.Event | None,
    approvals: asyncio.Queue | None,
) -> AsyncIterator[Event]:
    """Execute one Phase and record what it did. Never raises: a Phase's failure is its status."""
    record = current.phase(phase.dir_name)
    if record is None:
        return
    record.status, record.started_at, record.error = "running", time.time(), ""
    current.waiting = ""
    state.write(run, current)

    output = layout.phase_output(run, phase.dir_name)
    scratch = layout.phase_scratch(run, phase.dir_name)
    output.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    # One workspace per Run, so a later Phase finds an earlier one's CSV beside its script. It is
    # not shared with any chat, and it is forgotten when the Run ends unless the Run failed.
    workspace = f"flow-{current.flow}-{current.id}"

    try:
        material, missing = await _gather(flow, phase, run, workspace)
        if missing:
            raise RunFailed(f"input missing: {', '.join(missing)}")

        # Snapshotted after the inputs are pushed and before anything runs, so what comes back
        # is what *this* Phase wrote. One workspace per Run means an earlier Phase's files are
        # still sitting there, and without this they would be re-collected into every later
        # Phase's `scratch/`.
        already = await _listing(workspace)

        if phase.spec.kind == "script":
            async for event in _script_phase(phase, workspace, append):
                yield event
        else:
            async for event in _agent_phase(
                flow, phase, output, workspace, material, append, cancel, approvals
            ):
                yield event

        landed = await _collect(workspace, phase, output, scratch, already)
        absent = [name for name in phase.spec.outputs if not (output / name).is_file()]
        if absent:
            raise RunFailed(f"declared output not produced: {', '.join(absent)}")
        record.outputs = landed
        record.status = "done"
    except RunFailed as exc:
        record.status, record.error = "failed", str(exc)
        failure = Event(type="error", data={"message": str(exc), "phase": phase.dir_name})
        await append(phase.dir_name, failure)
        yield failure
    except (SandboxError, OSError) as exc:
        record.status, record.error = "failed", f"{type(exc).__name__}: {exc}"
        failure = Event(type="error", data={"message": record.error, "phase": phase.dir_name})
        await append(phase.dir_name, failure)
        yield failure
    finally:
        record.ended_at = time.time()
        # Re-running a Phase invalidates what came after it: the output is still there and still
        # readable, and what is gone is the claim that it follows from this.
        state.mark_stale_after(current, phase.dir_name)
        state.write(run, current)


class RunFailed(RuntimeError):
    """A Phase's own refusal — an input that is not there, an output that never appeared."""


async def _gather(
    flow: contract.Flow, phase: contract.Phase, run: Path, workspace: str
) -> tuple[prompt.Material, list[str]]:
    """Read the Phase's declared inputs, and push the Working ones to the sandbox.

    Both happen here because they are one question asked twice: which files does this Phase
    depend on. Pushing on every Phase rather than once per Run is what makes the **Gate** work —
    a person who edited `output/summary.md` at the Gate has to be the one the next Phase reads.
    """
    material = prompt.Material()
    missing: list[str] = []
    pushing: list[tuple[str, bytes]] = []

    for declared in phase.spec.inputs:
        resolved = layout.resolve_input(flow.directory, run, declared.kind, declared.path)
        if resolved is None or not resolved.is_file():
            missing.append(declared.path)
            continue
        if declared.kind == "reference":
            text = resolved.read_text(errors="replace")
            material.references.append(
                (declared.path, prompt.clip(text, declared.path, material.truncated))
            )
            continue
        # A Working input goes to the sandbox whatever it is, so `run_python` can reach it; what
        # differs is whether the model sees its words or only its shape.
        pushing.append((resolved.name, resolved.read_bytes()))
        if resolved.suffix.lower() in TABULAR:
            continue
        text = resolved.read_text(errors="replace")
        material.working_text.append(
            (declared.path, prompt.clip(text, declared.path, material.truncated))
        )

    if missing or not pushing:
        return material, missing

    async with await open_sandbox() as sandbox:
        # Deleted before it is pushed, and this is the line the **Gate** depends on. The sandbox
        # renames on collision — a second upload of `revenue.csv` lands as `revenue-2.csv` — and
        # one workspace per Run means the file is already there from the Phase that made it. So
        # without this, a person's edit at the Gate uploads to a *new* path and the next Phase's
        # script, asking for the name it was told, reads the version from before the edit. Found
        # by running a two-Phase flow and looking at what was in the workspace.
        found, _ = await sandbox.listing(workspace)
        existing = {path.rsplit("/", 1)[-1]: path for path in found}
        landed: set[str] = set()
        for name, data in pushing:
            if name in existing:
                with contextlib.suppress(SandboxError):
                    await sandbox.delete(workspace, existing[name])
            pushed = await sandbox.upload(workspace, name, data)
            landed.add(str(pushed.get("path") or name))

        # Only this Phase's own inputs. `profiles.saved` answers for the whole workspace, which is
        # every earlier Phase's files too — and a Phase seeing the shape of a file it never
        # declared is the scoping this design claims to do quietly not happening.
        for raw in await sandbox.profiles(workspace):
            shape = profiles.Profile.model_validate(raw)
            if shape.path in landed:
                material.working_shapes.append(shape.as_prompt())
    return material, missing


async def _script_phase(
    phase: contract.Phase, workspace: str, append: Sink
) -> AsyncIterator[Event]:
    """One named `.py`, posted to the sandbox. No model call at all.

    The events are authored here rather than by a tool, because there is no agent in this Phase to
    author them — but they are the same `tool_start`/`tool_end` pair, so the browser renders a
    script Phase with nothing new.
    """
    script = (phase.directory / phase.spec.script).read_text()
    began = Event(type="tool_start", data={"tool": "run_script", "label": phase.spec.script})
    await append(phase.dir_name, began)
    yield began

    async with await open_sandbox(timeout=None) as sandbox:
        result = await sandbox.run(workspace, script)

    shown = "\n".join(part for part in (result.out.rstrip(), result.err.rstrip()) if part)
    ended = Event(
        type="tool_end",
        data={
            "tool": "run_script",
            "ok": result.ok,
            "exit_code": result.code,
            "output": shown,
            "wrote": result.wrote[:10],
        },
    )
    await append(phase.dir_name, ended)
    yield ended
    if not result.ok:
        raise RunFailed(f"{phase.spec.script} exited {result.code}")


async def _agent_phase(
    flow: contract.Flow,
    phase: contract.Phase,
    output: Path,
    workspace: str,
    material: prompt.Material,
    append: Sink,
    cancel: asyncio.Event | None,
    approvals: asyncio.Queue | None,
) -> AsyncIterator[Event]:
    """One `run_turn`, with a prompt assembled from the folder and the Bundles the Phase asked for.

    The store session is opened per Phase and pinned to main. A Run is not a request and may sit
    at a Gate for an hour, so borrowing the app's session would mean holding it open across that.
    """
    async with await open_store() as store:
        bound = store.at()
        session = Session(
            catalog=await bound.catalog(),
            client=bound,
            max_rows=settings.max_rows,
            # No `chat_id`: a Run writes no `results` row and has no transcript. The workspace is
            # its own, which is the distinction `Session.workspace_key` exists to make.
            workspace=workspace,
            cancel=cancel,
            approvals=approvals,
        )
        instructions = prompt.build(flow, phase, material)

        async def build(_turn_id: str):
            model = build_model()
            return create_agent(
                model=model,
                tools=tools.build_phase_tools(session, phase, output),
                system_prompt=instructions,
                middleware=[
                    AuditLog(),
                    # `error` and not `end`: a Phase that ran out of model calls must fail rather
                    # than write its best guess into `output/` for the next Phase to build on.
                    ModelCallLimitMiddleware(run_limit=PHASE_MODEL_CALLS, exit_behavior="error"),
                    ToolCallLimitMiddleware(
                        tool_name="run_python",
                        run_limit=PHASE_PYTHON_CALLS,
                        exit_behavior="error",
                    ),
                ],
            )

        # The Phase's own thread, so a Phase is a scoped task rather than a conversation that
        # remembers the last Run of the same Flow.
        thread = f"{workspace}:{phase.dir_name}"
        failed = False
        async for event in run_turn(build, append, thread, "Begin this Phase.", cancel):
            if event.type == "error":
                failed = True
            yield event
        if failed:
            raise RunFailed("the phase's agent ended in an error")


async def _listing(workspace: str) -> set[str]:
    """What is in the Run's workspace now. An absent sandbox is an empty set: a prose Phase never
    opens one, and a Phase must not fail for not having used a service it did not need."""
    try:
        async with await open_sandbox() as sandbox:
            found, _ = await sandbox.listing(workspace)
    except SandboxError:
        return set()
    return set(found)


async def _collect(
    workspace: str,
    phase: contract.Phase,
    output: Path,
    scratch: Path,
    already: set[str],
) -> list[str]:
    """Bring the sandbox's files back: declared outputs to `output/`, everything else to
    `scratch/`.

    Only `output/` is visible to the next Phase, which is what keeps the declaration meaningful.
    `scratch/` exists because a chart nobody thought to declare is still worth keeping, and
    discarding it silently would be the first surprise of every new Flow.

    **A file that came back from the sandbox wins over one `write_output` wrote.** Seen on the
    first real run: the agent called `export_result` to make `revenue.csv` and then wrote the same
    name with `write_output`, having retyped the rows out of `run_query`'s reply. The two matched
    that time. One is data that went through Cube and one is data a model transcribed, and the
    transcription must not be the copy the next Phase reads.
    """
    landed = [name for name in phase.spec.outputs if (output / name).is_file()]
    try:
        async with await open_sandbox() as sandbox:
            found, _ = await sandbox.listing(workspace)
            for path in sorted(set(found) - already):
                name = path.rsplit("/", 1)[-1]
                target = output / name if name in phase.spec.outputs else scratch / name
                data, _ = await sandbox.read(workspace, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                if name in phase.spec.outputs and name not in landed:
                    landed.append(name)
    except SandboxError:
        # A Phase that never touched the sandbox is the common case for a prose Phase, and an
        # absent sandbox must not fail one. A missing declared output is caught by the caller.
        return landed
    return landed


async def forget(flow_name: str, run_id: str) -> None:
    """Drop the Run's sandbox workspace. Called when a Run ends well.

    Not on failure: the workspace is where a failed Phase's half-written files are, and that is
    the one time somebody wants to look. Portal disk keeps every Run either way — the sandbox's
    volume is not ours and cannot be browsed from the Runs page.
    """
    with contextlib.suppress(SandboxError):
        async with await open_sandbox() as sandbox:
            await sandbox.forget(f"flow-{flow_name}-{run_id}")
