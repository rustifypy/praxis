"""`run.json` — where a Run is, and which Phases can still be trusted.

The paper says the filesystem is the state, and for "which Phases have produced output" it is:
`ls` answers that. What `ls` cannot say is *paused at the Gate after Phase 2*, *waiting on an
approval*, or *Phase 3 failed at 14:02* — so those are here, in a file beside the artifacts they
describe. Still a plain file anyone can read; still no row.

The other reason this is a file rather than something derived: the browser must not recompute a
Run's state from its event log. One rule, one place, shipped as it is.
"""

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from . import layout

# `stale` is the one worth explaining: the output is still on disk and still readable, and what
# is gone is the claim that it follows from what is above it. Not `invalid` — it is not wrong.
PhaseStatus = Literal["pending", "running", "done", "failed", "stale"]
RunStatus = Literal["running", "paused", "done", "failed"]
# Why a paused Run is paused, and a **Gate** is the only answer that can be written down. An
# approval pause is a queue inside a running Phase: nothing here can record it, and a restart
# while a script waits fails that Phase rather than resuming it — `docs/adr/0006`. So "approval"
# is deliberately absent, because a state this file can never hold would be a lie to every reader
# of it. A waiting approval is known from the live event stream and nowhere else.
Waiting = Literal["", "gate"]


class PhaseState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dir_name: str
    status: PhaseStatus = "pending"
    started_at: float = 0.0
    ended_at: float = 0.0
    error: str = ""
    # What actually landed in `output/`, which is not always what was declared: the difference is
    # why a Phase can fail after its model call succeeded.
    outputs: list[str] = []


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    flow: str
    status: RunStatus = "running"
    # The Phase folder this Run is at. Empty once the Run is over.
    current: str = ""
    waiting: Waiting = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    error: str = ""
    phases: list[PhaseState] = []

    def phase(self, dir_name: str) -> PhaseState | None:
        return next((p for p in self.phases if p.dir_name == dir_name), None)


def begin(run_id: str, flow: str, dir_names: list[str]) -> RunState:
    """A Run at Phase one, with every Phase pending.

    The Phase list is fixed here, from the folders as they are *now*. A Flow edited mid-Run would
    otherwise change what the Run is, and "which Phases did this Run have" is a question its own
    record has to answer months later.
    """
    return RunState(
        id=run_id,
        flow=flow,
        status="running",
        current=dir_names[0] if dir_names else "",
        started_at=time.time(),
        phases=[PhaseState(dir_name=name) for name in dir_names],
    )


def mark_stale_after(state: RunState, dir_name: str) -> list[str]:
    """Every *done* Phase after `dir_name` becomes **Stale**. Returns the ones that changed.

    Called when a Phase is re-run or its output is edited. Only `done` is touched: a `failed`
    Phase is already not to be trusted and saying so twice tells nobody anything, and a `pending`
    one has produced nothing to go stale. Staleness does not cascade further than this because it
    does not need to — one pass over an ordered list is every Phase downstream.
    """
    names = [p.dir_name for p in state.phases]
    if dir_name not in names:
        return []
    after = names.index(dir_name) + 1
    changed = []
    for phase in state.phases[after:]:
        if phase.status == "done":
            phase.status = "stale"
            changed.append(phase.dir_name)
    return changed


def write(run: Path, state: RunState) -> None:
    """Replace `run.json` atomically.

    Written to a neighbour and renamed because this file *is* the Run's state: a crash halfway
    through a plain write leaves truncated JSON, and a Run whose state cannot be parsed is a Run
    nobody can resume or even describe. `replace` is atomic on one filesystem, which is where a
    temporary file beside the target is guaranteed to be.
    """
    run.mkdir(parents=True, exist_ok=True)
    target = run / layout.RUN_JSON
    staging = run / f".{layout.RUN_JSON}.tmp"
    staging.write_text(state.model_dump_json(indent=2))
    staging.replace(target)


def read(run: Path) -> RunState | None:
    """The Run's state, or `None` if there is none to read.

    A folder without a readable `run.json` is not an error here. It is either a Run from a build
    that wrote a different shape or a directory somebody made by hand, and the Runs list has to
    keep rendering either way — so the caller gets `None` and shows what it can.
    """
    try:
        return RunState.model_validate_json((run / layout.RUN_JSON).read_text())
    except (OSError, ValueError):
        return None
