"""One job at a time, and the action that starts it.

A job is subscribed to rather than awaited: starting a step answers immediately and the
narration arrives on `/job/stream`, so a three-minute describe does not hold a request open
and a page refresh re-attaches instead of losing the progress.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core import jobs as job_runner
from ..datamodel import modelgen
from ..datamodel.evidence import STAGES
from ..datamodel.modelgen.steps import StepFailed
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
    job = request.app.state.jobs.job
    return job.snapshot() if job else {"id": None}


@router.post("/job/cancel")
async def cancel_job(request: Request) -> dict:
    return {"cancelled": request.app.state.jobs.cancel()}


@router.get("/job/stream")
async def job_stream(request: Request) -> StreamingResponse:
    """SSE. Native EventSource on the browser side: a job is subscribed to, not posted."""
    return sse(request.app.state.jobs.watch())


@router.post("/source/actions/{action}")
async def run_action(action: str, body: RunStep, request: Request) -> dict:
    """Start a job that runs one action, and answer as soon as it exists."""
    if action not in STAGES:
        raise HTTPException(404, f"no such action {action!r} — one of {', '.join(STAGES)}")
    # Bound out here so the closure does not hold the finished request — and with it the
    # whole ASGI scope — alive for the minutes a describe takes.
    app = request.app
    runner = app.state.jobs
    store = app.state.sources

    async def work(job: job_runner.Job) -> None:
        job.current = action
        runner.emit("step_start", step=action)
        try:
            result = await modelgen.steps.action(
                action,
                store,
                schema=body.schema_name,
                tables=body.tables,
                report=lambda stage, payload: runner.emit("progress", step=stage, **payload),
            )
        except StepFailed as exc:
            # Cube's own refusal is in the message; there are no container logs to add, since
            # the deployment runs wherever its owner put it.
            runner.emit("step_end", step=action, ok=False, error=str(exc))
            raise
        finally:
            # A rebuild changes what Cube serves and nothing watches it for that.
            app.state.health = None
            job.current = ""
        runner.emit(
            "step_end",
            step=action,
            ok=result.ok,
            summary=result.summary,
            notes=result.notes,
            errors=result.errors,
        )

    try:
        job = await runner.start([action], work)
    except job_runner.Busy as exc:
        raise HTTPException(409, f"a job is already running — {exc}") from None
    return job.snapshot()
