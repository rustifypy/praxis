"""One job at a time, and the action that starts it.

A job is subscribed to rather than awaited: starting a step answers immediately and the
narration arrives on `/job/stream`, so a three-minute describe does not hold a request open
and a page refresh re-attaches instead of losing the progress.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..sources import docker, model
from ..sources import jobs as job_runner
from ..sources.model.run import StepFailed
from ..sources.store import STAGES
from .state import find, invalidate, runner_of, store_of
from .stream import sse

router = APIRouter(prefix="/api")


class RunStep(BaseModel):
    schema_name: str = "public"
    tables: list[str] | None = None
    """None models every table in the schema."""


# --- the job -----------------------------------------------------------------


@router.get("/job")
async def current_job(request: Request) -> dict:
    """The running job and its buffered events, so a refresh re-attaches."""
    job = runner_of(request).job
    return job.snapshot() if job else {"id": None}


@router.post("/job/cancel")
async def cancel_job(request: Request) -> dict:
    return {"cancelled": runner_of(request).cancel()}


@router.get("/job/stream")
async def job_stream(request: Request) -> StreamingResponse:
    """SSE. Native EventSource on the browser side: a job is subscribed to, not posted."""
    return sse(runner_of(request).watch())


@router.post("/sources/{name}/actions/{name_of_action}")
async def run_action(name: str, name_of_action: str, body: RunStep, request: Request) -> dict:
    """Start a job that runs one action, and answer as soon as it exists."""
    if name_of_action not in STAGES:
        raise HTTPException(404, f"no such action {name_of_action!r} — one of {', '.join(STAGES)}")
    source = await find(request, name)
    runner = runner_of(request)
    store = store_of(request)
    # Bound out here so the closure does not hold the finished request — and with it the
    # whole ASGI scope — alive for the minutes a describe takes.
    cache = request.app.state.health

    async def work(job: job_runner.Job) -> None:
        job.current = name_of_action
        runner.emit("step_start", step=name_of_action)
        try:
            result = await model.run.action(
                name_of_action,
                source,
                store,
                schema=body.schema_name,
                tables=body.tables,
                report=lambda stage, payload: runner.emit("progress", step=stage, **payload),
            )
        except StepFailed as exc:
            runner.emit("step_end", step=name_of_action, ok=False, error=str(exc))
            # The container is left as it is — its logs are the diagnosis.
            runner.emit("log", message=await docker.logs(source.service, 40))
            raise
        finally:
            invalidate(cache, source)
            job.current = ""
        runner.emit(
            "step_end",
            step=name_of_action,
            ok=result.ok,
            summary=result.summary,
            notes=result.notes,
            errors=result.errors,
        )

    try:
        job = await runner.start(source.name, [name_of_action], work)
    except job_runner.Busy as exc:
        raise HTTPException(409, f"a job is already running — {exc}") from None
    return job.snapshot()
