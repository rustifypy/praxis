"""A Run's event log: `runs/<id>/events.jsonl`, one JSON object per line.

The same `Event` a chat writes, in the same order, with the same types — so the browser's
`applyEvent` and `Transcript.tsx` render a Run without learning anything new. What differs is
only where it is kept, which is `agent.turn.Sink`'s whole reason for existing.

Append-only, one line at a time, flushed on every write. A Run can be minutes long and the log is
what someone watches while it is; a buffered writer would show an empty file until the Phase
ended. It is also the audit trail after the fact, so a crash must keep what had already happened
rather than losing the tail.

The line shape mirrors the `events` table's columns, `chat_id` aside. That is deliberate: the two
sinks should be diffable, and a reader written against one should not surprise anyone against the
other.
"""

import json
import time
from pathlib import Path

from ..agent.turn import Sink
from ..core.events import Event
from . import layout


def sink(run: Path) -> Sink:
    """The Run's log as a sink. `seq` is counted from the file, not held in memory, so a Phase
    re-run after a restart continues the numbering rather than starting again at one."""

    async def append(turn_id: str, event: Event) -> None:
        run.mkdir(parents=True, exist_ok=True)
        target = run / layout.EVENTS_JSONL
        line = {
            "id": event.id,
            "seq": _count(target) + 1,
            "turn_id": turn_id,
            "type": event.type,
            "data": event.data,
            "parent_id": event.parent_id,
            "depth": event.depth,
            "at": time.time(),
        }
        with target.open("a") as handle:
            handle.write(json.dumps(line, default=str) + "\n")

    return append


def _count(target: Path) -> int:
    try:
        with target.open() as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def read(run: Path) -> list[dict]:
    """Every event of a Run, in order.

    A line that will not parse is skipped rather than raising. The log is appended to while a
    Phase runs, so a reader can arrive mid-write and see half a line — and a Run's history is
    worth more than strictness about its last byte.
    """
    try:
        with (run / layout.EVENTS_JSONL).open() as handle:
            lines = handle.readlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            found.append(parsed)
    return found
