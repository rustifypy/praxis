"""Every path a Flow has, and the only place that joins path components.

One module rather than paths spelled at each call site, for the reason `core.db.SCHEMA` is one
module: these names are a format somebody else's folder is already in. A Flow copied from another
machine has to be readable here, and a Run written by an older build has to be readable by a newer
one, so the strings below are the compatibility surface and changing one breaks someone's folder.

The root is passed in rather than looked up. Where flows live is a settings question and this
module answers path questions — which also lets a test point it at a temporary directory without
touching the environment.
"""

import re
from pathlib import Path

# The definition.
FLOW_YML = "flow.yml"
CONTEXT_MD = "CONTEXT.md"
PHASE_YML = "phase.yml"
PHASES = "phases"
CONFIG = "_config"
REFERENCES = "references"
# A Phase's prepared Cube queries, one `<name>.json` each.
QUERIES = "queries"
# The flow chat's id. In the folder and not on `chats`, because there is no migration mechanism
# and a new column would cost every existing user their database — see `docs/adr/0006`.
CHAT_JSON = "chat.json"
# One Run at a time per Flow. Held here rather than by `core.jobs`, whose lock is machine-wide and
# would let a long Run block a model rebuild.
LOCK = "run.lock"

# One Run.
RUNS = "runs"
LATEST = "latest"
RUN_JSON = "run.json"
EVENTS_JSONL = "events.jsonl"
BRIEF_MD = "brief.md"
# Declared outputs, and everything else the script wrote. Only `output/` is visible to the next
# Phase; `scratch/` exists because a chart nobody declared is still worth keeping.
OUTPUT = "output"
SCRATCH = "scratch"

# `NN_name`. The number is the execution order; the name is for whoever reads `ls`.
PHASE_DIR = re.compile(r"^(\d{2,})_([a-z0-9][a-z0-9_]*)$")
# A Flow's folder name is its identity, so it has to survive being typed, sorted and put in a URL.
FLOW_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def flow_dir(root: Path, flow: str) -> Path:
    return root / flow


def phases_dir(flow: Path) -> Path:
    return flow / PHASES


def runs_dir(flow: Path) -> Path:
    return flow / RUNS


def run_dir(flow: Path, run_id: str) -> Path:
    return flow / RUNS / run_id


def run_ids(flow: Path) -> list[str]:
    """Every Run of this Flow, newest first.

    Symlinks are skipped, and that is the whole reason this is a function: `runs/latest` points at
    a real Run directory, so `is_dir()` is true for it and a plain listing returns that Run twice
    — once under its id and once as `latest`. The ids are timestamped, so sorting them by name is
    sorting them by time.
    """
    here = runs_dir(flow)
    if not here.is_dir():
        return []
    found = [p.name for p in here.iterdir() if p.is_dir() and not p.is_symlink()]
    return sorted(found, reverse=True)


def phase_output(run: Path, dir_name: str) -> Path:
    return run / dir_name / OUTPUT


def phase_scratch(run: Path, dir_name: str) -> Path:
    return run / dir_name / SCRATCH


def phase_number(dir_name: str) -> int | None:
    """The execution order, read off the folder name. `None` means the folder is not a Phase,
    which is not an error at this level — a Flow may keep notes beside its phases."""
    found = PHASE_DIR.match(dir_name)
    return int(found.group(1)) if found else None


def phase_dirs(flow: Path) -> list[str]:
    """Every Phase folder, in execution order.

    Sorted by the parsed number rather than the string, so `10` follows `02` the day someone
    numbers past nine — which is also why leaving gaps does not work the way it looks like it
    should: `015` is fifteen, and lands after `02` rather than between it and `01`.

    Flows are numbered consecutively — `01`, `02`, `03` — so a folder's name is its position,
    and that is what the authoring agent is told to write. Gaps are read without complaint all
    the same, because a hand-edited Flow is still a Flow. Inserting a step means renaming the
    folders after it and every `working:` path naming them. A path missed in that rename fails its
    Phase as a missing input — `contract.load` checks only that the number is earlier — so it is
    loud, but at run time rather than at write time.
    """
    here = phases_dir(flow)
    if not here.is_dir():
        return []
    numbered = [(phase_number(p.name), p.name) for p in here.iterdir() if p.is_dir()]
    return [name for _, name in sorted((n, m) for n, m in numbered if n is not None)]


def contained(base: Path, candidate: Path) -> bool:
    """Whether `candidate` stays inside `base` once `..` has had its say.

    The rule `workspace` applies to a model file, for the same reason: the authoring agent and the
    HTTP route must be refused in the same words, so both ask here. Resolved rather than compared
    as text, because `flows/a/../../etc` is inside nothing until it is normalised.
    """
    try:
        here, there = base.resolve(), candidate.resolve()
    except OSError:
        return False
    return here == there or here in there.parents


def writable(path: str) -> str:
    """Why this path may not be written as part of a Flow's definition, or `""` if it may.

    A Flow's folder holds two unrelated things: the definition, which people and the flow agent
    edit, and everything a Run produced, which is a record. This is the line between them, kept
    here so the authoring agent and the HTTP route are refused in the same words — the rule
    `workspace` applies to `cubes/*.yml` and `views/*.yml`, for the reason it applies it.

    `runs/` is the interesting refusal. A Run's artifacts *are* editable, but at a **Gate** and
    through the route that knows which Phase is paused — not as a file in the Flow, which would
    let a Phase's output be rewritten while the Phase after it was reading it.
    """
    if not path or path.startswith("/") or "\\" in path:
        return f"{path!r} is not a path inside the flow"
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"{path!r} may not contain '.' or '..'"
    if parts[0] in (RUNS, CHAT_JSON, LOCK):
        return f"{parts[0]} is a record of what ran, not part of the flow's definition"
    if len(parts) == 1:
        if path in (FLOW_YML, CONTEXT_MD):
            return ""
        return f"{path} is not one of the flow's own files ({FLOW_YML}, {CONTEXT_MD})"
    if parts[0] == CONFIG:
        return ""
    if parts[0] != PHASES:
        return f"{parts[0]}/ is not part of a flow — use {CONFIG}/ or {PHASES}/NN_name/"
    if phase_number(parts[1]) is None:
        return f"{parts[1]} is not a phase folder — the name is NN_name, and NN is the order"
    if len(parts) == 2:
        return f"{path} is a folder, not a file"
    if len(parts) == 3 and (parts[2] in (PHASE_YML, CONTEXT_MD) or parts[2].endswith(".py")):
        return ""
    if parts[2] == REFERENCES:
        return ""
    if parts[2] == QUERIES and len(parts) == 4 and parts[3].endswith(".json"):
        return ""
    return (
        f"{parts[2]} is not something a phase holds — {PHASE_YML}, {CONTEXT_MD}, "
        f"a .py file, {REFERENCES}/, or {QUERIES}/<name>.json"
    )


def resolve_input(flow: Path, run: Path, kind: str, path: str) -> Path | None:
    """A declared input's file, or `None` if it escapes the root it belongs to.

    This is the layer distinction and the whole of it. A **Reference** resolves against the Flow
    and is identical in every Run; a **Working** input resolves against the Run and exists only
    because this Run produced it. Nothing downstream re-decides which root a path belongs to,
    which is what stops a Phase reading last Tuesday's output by accident.
    """
    base = flow if kind == "reference" else run
    candidate = base / path
    return candidate if contained(base, candidate) else None
