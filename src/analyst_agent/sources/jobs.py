"""One job at a time, held in memory, watched over SSE."""

import asyncio
import contextlib
import fcntl
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from ..config import settings

BUFFER = 400
"""Events kept for a reconnecting browser. A step emits a handful; describing four
hundred tables emits four hundred, and only the tail is worth replaying."""
LOCK_NAME = "jobs.lock"


class Busy(RuntimeError):
    """Another job holds the lock. Carries what it is, so the message can say so."""


@contextlib.contextmanager
def hold(description: str):
    """Take the machine-wide job lock, or raise `Busy` naming the current holder."""
    path = settings.dir_path / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            holder = handle.read().strip() or "another process"
            raise Busy(holder) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{description} (pid {os.getpid()})")
        handle.flush()
        yield
    finally:
        handle.close()


@dataclass
class Event:
    """One line of a job's narration. Not the chat `Event` — a job is not a turn."""

    type: str
    """job_start | step_start | progress | log | step_end | job_end"""
    data: dict = field(default_factory=dict)
    seq: int = 0
    at: float = 0.0


@dataclass
class Job:
    id: str
    source: str
    steps: list[str]
    """Which steps this job will run, in order, so the UI can draw the whole path."""
    started_at: float
    events: list[Event] = field(default_factory=list)
    done: bool = False
    error: str = ""
    current: str = ""
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    watchers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "steps": self.steps,
            "current": self.current,
            "started_at": self.started_at,
            "done": self.done,
            "error": self.error,
            "events": [e.__dict__ for e in self.events],
        }


class Runner:
    """Owns the current job. One instance on the app, created at startup."""

    def __init__(self):
        self.job: Job | None = None
        self._seq = 0

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.done

    def emit(self, type: str, **data) -> Event:
        """Append an event and hand it to every watcher. Never blocks on a slow reader."""
        job = self.job
        if job is None:
            return Event(type=type, data=data)
        self._seq += 1
        event = Event(type=type, data=data, seq=self._seq, at=time.time())
        job.events.append(event)
        del job.events[:-BUFFER]
        for queue in list(job.watchers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    async def watch(self) -> AsyncIterator[Event]:
        """Every event of the current job: the buffer first, then live ones."""
        job = self.job
        if job is None:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=BUFFER)
        # Registered before the buffer is read, so nothing emitted in between is lost.
        # That can duplicate an event, which `seen` filters on the way out.
        job.watchers.add(queue)
        try:
            seen = 0
            for event in list(job.events):
                seen = event.seq
                yield event
            if job.done:
                return
            while True:
                event = await queue.get()
                if event.seq <= seen:
                    continue
                seen = event.seq
                yield event
                if event.type == "job_end":
                    return
        finally:
            job.watchers.discard(queue)

    def cancel(self) -> bool:
        if self.job is None or self.job.done:
            return False
        self.job.cancel.set()
        return True

    async def start(
        self,
        source: str,
        steps: list[str],
        work: Callable[[Job], Awaitable[None]],
    ) -> Job:
        """Take the lock, publish a Job, and run `work` in the background."""
        if self.busy:
            raise Busy(f"{self.job.source}: {self.job.current or 'starting'}")

        description = f"{source}: {', '.join(steps)}"
        # Taken here, before returning, so a second request is refused rather than
        # queued behind a lock it would block the event loop waiting for.
        guard = hold(description)
        guard.__enter__()

        job = Job(id=uuid.uuid4().hex, source=source, steps=list(steps), started_at=time.time())
        self.job = job
        self._seq = 0
        self.emit("job_start", source=source, steps=job.steps)

        async def run() -> None:
            try:
                await work(job)
            except asyncio.CancelledError:
                job.error = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001 — a failed job is a UI state, not a 500
                job.error = str(exc)
            finally:
                job.done = True
                self.emit("job_end", error=job.error, cancelled=job.cancel.is_set())
                guard.__exit__(None, None, None)

        job.task = asyncio.create_task(run())
        return job
