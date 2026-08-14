"""The job runner: one at a time, watchable, and cancellable.

No containers and no model calls — the work function is a stand-in. What is being
checked is the machinery a real job runs inside.
"""

import asyncio

import pytest

from analyst_agent.core import jobs


@pytest.fixture(autouse=True)
def scratch_lock(tmp_path, monkeypatch):
    """The lock lives in the data directory, so point that somewhere disposable."""
    from analyst_agent.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))


async def drain(runner: jobs.Runner) -> list[jobs.JobLine]:
    return [event async for event in runner.watch()]


async def test_a_job_publishes_start_and_end():
    runner = jobs.Runner()

    async def work(job):
        runner.emit("step_start", step="connect")
        runner.emit("step_end", step="connect", ok=True)

    job = await runner.start("shop", ["connect"], work)
    await job.task
    assert [e.type for e in job.events] == ["job_start", "step_start", "step_end", "job_end"]
    assert job.done and not job.error


async def test_a_failing_step_becomes_the_jobs_error_not_an_exception():
    runner = jobs.Runner()

    async def work(job):
        raise RuntimeError("Cube rejected the model")

    job = await runner.start("shop", ["cubes"], work)
    await job.task
    assert job.done
    assert job.error == "Cube rejected the model"
    assert job.events[-1].type == "job_end"
    assert job.events[-1].data["error"] == "Cube rejected the model"


async def test_a_second_job_is_refused_while_one_runs():
    runner = jobs.Runner()
    release = asyncio.Event()

    async def work(job):
        await release.wait()

    first = await runner.start("shop", ["describe"], work)
    with pytest.raises(jobs.Busy):
        await runner.start("other", ["connect"], work)
    release.set()
    await first.task
    # Once it finishes the runner is free again.
    await (await runner.start("other", ["connect"], lambda job: asyncio.sleep(0))).task


async def test_the_lock_names_what_is_holding_it():
    """So the refusal can say 'shop: describe' rather than just 'busy'."""
    with jobs.hold("shop: describe"):
        with pytest.raises(jobs.Busy, match="shop: describe"):
            with jobs.hold("other: connect"):
                pass


async def test_the_lock_is_released_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with jobs.hold("shop: cubes"):
            raise ValueError("boom")
    # Would raise Busy if the first hold had leaked.
    with jobs.hold("shop: cubes"):
        pass


async def test_a_watcher_gets_the_buffer_then_the_live_events():
    """This is what makes a page refresh mid-job seamless."""
    runner = jobs.Runner()
    started = asyncio.Event()
    release = asyncio.Event()

    async def work(job):
        runner.emit("step_start", step="describe")
        started.set()
        await release.wait()
        runner.emit("progress", done=7, total=12)

    job = await runner.start("shop", ["describe"], work)
    await started.wait()

    watcher = asyncio.create_task(drain(runner))
    await asyncio.sleep(0)
    release.set()
    await job.task
    events = await watcher

    types = [e.type for e in events]
    assert types[:2] == ["job_start", "step_start"], "the buffer replays first"
    assert "progress" in types, "then live events arrive"
    assert types[-1] == "job_end", "and the stream closes itself"


async def test_a_watcher_never_sees_the_same_event_twice():
    """The queue is registered before the buffer is read, which can duplicate one."""
    runner = jobs.Runner()
    release = asyncio.Event()

    async def work(job):
        for i in range(5):
            runner.emit("progress", done=i)
        await release.wait()

    job = await runner.start("shop", ["describe"], work)
    watcher = asyncio.create_task(drain(runner))
    await asyncio.sleep(0)
    release.set()
    await job.task
    events = await watcher
    assert [e.seq for e in events] == sorted({e.seq for e in events})


async def test_watching_a_finished_job_replays_it_and_stops():
    runner = jobs.Runner()
    job = await runner.start("shop", ["connect"], lambda job: asyncio.sleep(0))
    await job.task
    assert [e.type for e in await drain(runner)] == ["job_start", "job_end"]


async def test_cancel_stops_the_next_step_not_the_one_in_flight():
    """A step is a container restart or a model call; it finishes, then the job stops."""
    runner = jobs.Runner()
    seen = []
    in_first_step = asyncio.Event()
    release = asyncio.Event()

    async def work(job):
        for step in ("connect", "cubes", "describe"):
            if job.cancel.is_set():
                break
            seen.append(step)
            in_first_step.set()
            await release.wait()

    job = await runner.start("shop", ["connect", "cubes", "describe"], work)
    await in_first_step.wait()
    assert runner.cancel()
    release.set()
    await job.task
    assert seen == ["connect"]
    assert job.events[-1].data["cancelled"] is True


async def test_cancelling_when_nothing_runs_is_a_no_op():
    assert jobs.Runner().cancel() is False


async def test_the_buffer_is_bounded_so_a_wide_schema_cannot_grow_it_forever(monkeypatch):
    monkeypatch.setattr(jobs, "BUFFER", 10)
    runner = jobs.Runner()

    async def work(job):
        for i in range(50):
            runner.emit("progress", done=i)

    job = await runner.start("shop", ["describe"], work)
    await job.task
    assert len(job.events) == 10
    # The tail is what is kept — the end of a job is what a late watcher needs.
    assert job.events[-1].type == "job_end"
