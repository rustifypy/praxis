"""The six pipeline routes.

**Order is load-bearing here more than anywhere else in the app.** `POST /{stage}` is as broad as
a path parameter gets, so it must stay below `/tables` and `/job` — registered first, it would
answer for both. `test_surface.py` asserts that now, but the ordering still has to be read as
deliberate rather than incidental.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...core import jobs as job_runner
from ...modelstore.modelgen.evidence import STAGES
from ..support.stream import sse
from . import controller
from .dto import Cancelled, Closure, JobSnapshot, StageRequest, TableCatalog

router = APIRouter(prefix="/api/pipeline")


@router.get("/tables", response_model=TableCatalog)
async def read_tables() -> TableCatalog:
    """The warehouse's tables as the `read` stage cached them, one row per table for the picker.
    Never opens the database: this is answered on every page build and on every checkbox tick."""
    return await controller.cached_tables()


@router.post("/tables/closure", response_model=Closure)
async def read_closure(body: StageRequest) -> Closure:
    """The tables a selection drags in through its joins, plus what it cannot join at all.
    404 when that schema has not been read — there is nothing cached to close over."""
    found = await controller.close_over(body.schema_name, body.tables)
    if found is None:
        raise HTTPException(404, f"schema {body.schema_name!r} has not been read")
    return found


@router.get("/job", response_model=JobSnapshot)
async def read_job(request: Request) -> JobSnapshot:
    """The running job and its buffered events, so a page refresh re-attaches to the narration.
    Answers `{"id": null}` and never a 404 when nothing is running: nothing is wrong."""
    return controller.running(request.app.state.jobs)


@router.delete("/job", response_model=Cancelled)
async def cancel_job(request: Request) -> Cancelled:
    """Ask the running job to stop, and say whether there was one.
    False is a normal answer, not an error — the button can always land after the job ended."""
    return controller.cancel(request.app.state.jobs)


@router.get("/job/stream")
async def stream_job(request: Request) -> StreamingResponse:
    """The running job's narration as server-sent events, replayed from its buffer on connect.
    No `response_model`: a job is subscribed to rather than awaited, so a three-minute describe
    does not hold a request open."""
    return sse(request.app.state.jobs.watch())


# Below every fixed-segment route above it. See the module docstring.
@router.post("/{stage}", response_model=JobSnapshot)
async def run_stage(stage: str, body: StageRequest, request: Request) -> JobSnapshot:
    """Start one stage as a job and answer with its snapshot as soon as the job exists.
    404 for a name that is not one of the four stages, and 409 when a job is already running —
    one at a time, machine-wide."""
    if stage not in STAGES:
        raise HTTPException(404, f"no such stage {stage!r} — one of {', '.join(STAGES)}")
    try:
        return await controller.start(request.app, stage, body.schema_name, body.tables)
    except job_runner.Busy as exc:
        raise HTTPException(409, f"a job is already running — {exc}") from None
