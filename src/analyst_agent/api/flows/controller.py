"""The reads the flows page needs, and the two writes that are not just "put this file there".

Assembling a Run for the browser lives here rather than in `flows/`: it is a screen's shape — the
phases with their artifacts and which one may be edited — and `flows/` should not know a screen
exists. What it does *not* recompute is anything `run.json` already says.
"""

from pathlib import Path

from fastapi import HTTPException

from ...flows import chat, contract, events, layout, runner, state


def flow_or_404(name: str) -> Path:
    directory = layout.flow_dir(runner.root(), name)
    if not (directory / layout.FLOW_YML).is_file():
        raise HTTPException(404, f"no flow named {name!r}")
    return directory


def run_or_404(directory: Path, run_id: str) -> tuple[Path, state.RunState]:
    run = layout.run_dir(directory, run_id)
    current = state.read(run)
    if current is None:
        raise HTTPException(404, f"no run {run_id!r}")
    return run, current


def definition_files(directory: Path) -> list[str]:
    """Every file the Flow's definition is made of. `runs/` is not among them — `layout.writable`
    is the one place that draws that line, and this asks it rather than filtering by name."""
    found = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(directory))
        if not layout.writable(relative):
            found.append(relative)
    return sorted(found)


def editable_phase(current: state.RunState) -> str:
    """The one Phase whose output may be edited right now, or `""`.

    Only while the Run is paused at that Phase's **Gate**, which is the Phase immediately before
    the one about to run. Editing an earlier Phase's output while a later Phase is running would
    produce an inconsistency nothing would ever surface — the Phase would already have read the
    old bytes.
    """
    if current.status != "paused" or current.waiting != "gate":
        return ""
    names = [p.dir_name for p in current.phases]
    if current.current not in names:
        return ""
    at = names.index(current.current)
    return names[at - 1] if at else ""


def run_detail(directory: Path, run_id: str) -> dict:
    run, current = run_or_404(directory, run_id)
    phases = []
    for record in current.phases:
        phases.append(
            {
                **record.model_dump(),
                # What is actually on disk, which is not always what was declared: a Phase can
                # fail after its model call succeeded, and the difference is the reason.
                "output": _names(layout.phase_output(run, record.dir_name)),
                "scratch": _names(layout.phase_scratch(run, record.dir_name)),
            }
        )
    return {
        **current.model_dump(exclude={"phases"}),
        "phases": phases,
        "brief": _text(run / layout.BRIEF_MD),
        "editable": editable_phase(current),
    }


def run_summaries(directory: Path) -> list[dict]:
    found = []
    for run_id in layout.run_ids(directory):
        current = state.read(layout.run_dir(directory, run_id))
        if current is None:
            continue
        found.append(current.model_dump(exclude={"phases"}) | {"phase_count": len(current.phases)})
    return found


def flow_detail(name: str) -> dict:
    directory = flow_or_404(name)
    flow, report = contract.load(directory)
    phases = []
    if flow is not None:
        phases = [
            {
                "dir_name": phase.dir_name,
                "number": phase.number,
                **phase.spec.model_dump(),
                # Rendered as declared, so a refusal names what the author wrote.
                "inputs": [
                    {"kind": declared.kind, "path": declared.path} for declared in phase.spec.inputs
                ],
            }
            for phase in flow.phases
        ]
    return chat.summary(name) | {"phases_detail": phases, "files": definition_files(directory)}


def write_file(directory: Path, path: str, text: str) -> dict:
    """Write one definition file, then re-check the Flow.

    The same two refusals the authoring agent gets, in the same words: `layout.writable` decides
    what a definition file is, and `contract.load` decides whether the Flow still runs. A route
    that spelled either itself would be a second answer to a question with one.
    """
    if refusal := layout.writable(path):
        raise HTTPException(400, refusal)
    target = directory / path
    if not layout.contained(directory, target):
        raise HTTPException(400, f"{path} resolves outside the flow folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _, report = contract.load(directory)
    return {"path": path, "ok": report.ok, "errors": report.errors, "warnings": report.warnings}


def artifact(run: Path, current: state.RunState, path: str, *, writing: bool) -> Path:
    """One of a Run's files, refused unless it is in a Phase's `output/` — and, for a write,
    unless that Phase is the one at the **Gate**.

    Reads are open across the Run because reading is how someone understands what happened.
    Writing is the paper's "every output is an edit surface" and is deliberately narrow: the
    surface exists for the moment before the next Phase reads it, and not afterwards.
    """
    target = run / path
    if not layout.contained(run, target):
        raise HTTPException(400, f"{path} resolves outside the run folder")
    parts = path.split("/")
    if len(parts) < 3 or parts[1] != layout.OUTPUT:
        raise HTTPException(400, f"{path} is not a phase's output — expected NN_name/output/<file>")
    if writing:
        allowed = editable_phase(current)
        if not allowed:
            raise HTTPException(409, "this run is not paused at a gate, so nothing is editable")
        if parts[0] != allowed:
            # Two different refusals, because the two are not the same mistake and a person who
            # is told the wrong one goes looking in the wrong place. Editing an *earlier* Phase's
            # output is too late — the Phase after it has read those bytes. Editing a *later*
            # one is too early: it has not run, so there is nothing there to edit.
            names = [p.dir_name for p in current.phases]
            asked = names.index(parts[0]) if parts[0] in names else len(names)
            reason = (
                f"{parts[0]} has not run yet, so it has no output to edit"
                if asked > names.index(allowed)
                else f"{parts[0]} has already been read by the phase after it"
            )
            raise HTTPException(409, f"only {allowed}'s output may be edited now — {reason}")
    elif not target.is_file():
        raise HTTPException(404, f"{path} is not there")
    return target


def run_events(directory: Path, run_id: str) -> list[dict]:
    run, _ = run_or_404(directory, run_id)
    return events.read(run)


def _names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.is_file()) if directory.is_dir() else []


def _text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""
