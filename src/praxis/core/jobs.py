import asyncio
import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from .config import settings
from .filelock import try_lock

BUFFER = 400
LOCK_NAME = "jobs.lock"


class Busy(RuntimeError):
    pass


@contextlib.contextmanager
def hold(description: str):
    path = settings.dir_path / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            try_lock(handle)
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
class JobLine:
    type: str
    data: dict = field(default_factory=dict)
    seq: int = 0
    at: float = 0.0


@dataclass
class Job:
    id: str
    steps: list[str]
    started_at: float
    events: list[JobLine] = field(default_factory=list)
    done: bool = False
    error: str = ""
    current: str = ""
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    watchers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "steps": self.steps,
            "current": self.current,
            "started_at": self.started_at,
            "done": self.done,
            "error": self.error,
            "events": [line.__dict__ for line in self.events],
        }


class Runner:
    def __init__(self):
        self.job: Job | None = None
        self._seq = 0

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.done

    def emit(self, line_type: str, **data) -> JobLine:
        job = self.job
        if job is None:
            return JobLine(type=line_type, data=data)
        self._seq += 1
        line = JobLine(type=line_type, data=data, seq=self._seq, at=time.time())
        job.events.append(line)
        del job.events[:-BUFFER]
        for queue in list(job.watchers):
            try:
                queue.put_nowait(line)
            except asyncio.QueueFull:
                pass
        return line

    async def watch(self) -> AsyncIterator[JobLine]:
        job = self.job
        if job is None:
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=BUFFER)
        # Registered before the buffer is read, so nothing emitted in between is lost. That
        # can duplicate a line, which `last_seen` filters on the way out.
        job.watchers.add(queue)
        try:
            last_seen = 0
            for line in list(job.events):
                last_seen = line.seq
                yield line
            if job.done:
                return
            while True:
                line = await queue.get()
                if line.seq <= last_seen:
                    continue
                last_seen = line.seq
                yield line
                if line.type == "job_end":
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
        steps: list[str],
        work: Callable[[Job], Awaitable[None]],
    ) -> Job:
        if self.busy:
            raise Busy(self.job.current or "starting")

        # Entered manually, before returning, so a second request is refused rather than
        # queued behind a lock it would block the event loop waiting for.
        guard = hold(", ".join(steps))
        guard.__enter__()

        job = Job(id=uuid.uuid4().hex, steps=list(steps), started_at=time.time())
        self.job = job
        self._seq = 0
        self.emit("job_start", steps=job.steps)

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
