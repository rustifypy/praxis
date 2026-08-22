"""Building the model: the four stages, the job running one, and picking the tables.

`/pipeline/tables` is the *warehouse's* tables, cached by the `read` stage — not
`cube.catalog.Catalog`, which is the served model and close to the opposite thing.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core import deployment as connection
from ..core import jobs as job_runner
from ..datamodel import modelgen
from ..datamodel.evidence import STAGES
from ..datamodel.modelgen import selection
from ..datamodel.modelgen.steps import StepFailed
from . import state
from .connection import find, read
from .stream import sse

router = APIRouter(prefix="/api/pipeline")


class RunStage(BaseModel):
    schema_name: str = "public"
    # None models every table in the schema.
    tables: list[str] | None = None


class TableSelection(BaseModel):
    # Its own model rather than the one a stage takes: the shape matches today, the question
    # does not.
    schema_name: str = "public"
    tables: list[str] | None = None


# --- the tables --------------------------------------------------------------


@router.get("/tables")
async def tables() -> dict:
    # What `read` cached, for the picker. Never touches the database.
    current = await connection.state()
    schemas = {
        schema: modelgen.warehouse.load_schema(raw)
        for schema, raw in (current.get("catalog") or {}).items()
    }
    return {
        "read": bool(schemas),
        "truncated": bool(current.get("catalog_truncated")),
        "notes": current.get("catalog_notes") or [],
        "selection": current.get("selection") or {},
        "schemas": [
            {"schema": name, "tables": selection.describe(schema)}
            for name, schema in sorted(schemas.items())
        ],
    }


@router.post("/tables/closure")
async def closure(body: TableSelection) -> dict:
    # Only the requested schema is rebuilt: this fires on every checkbox tick, and rebuilding
    # every schema would reconstruct every Column of each one each time.
    raw = (await connection.state()).get("catalog") or {}
    if body.schema_name not in raw:
        raise HTTPException(404, f"schema {body.schema_name!r} has not been read")
    schema = modelgen.warehouse.load_schema(raw[body.schema_name])
    picked = body.tables or []
    chosen = sorted(selection.closure(schema, picked))
    return {
        "tables": chosen,
        "added": [table for table in chosen if table not in picked],
        "cut_joins": selection.cut_joins(schema, chosen),
        "keyless": [table for table in selection.keyless(schema) if table in chosen],
    }


# --- the job -----------------------------------------------------------------


@router.get("/job")
async def current_job(request: Request) -> dict:
    """The running job and its buffered events, so a refresh re-attaches."""
    job = request.app.state.jobs.job
    return job.snapshot() if job else {"id": None}


@router.delete("/job")
async def cancel_job(request: Request) -> dict:
    return {"cancelled": request.app.state.jobs.cancel()}


@router.get("/job/stream")
async def job_stream(request: Request) -> StreamingResponse:
    # A job is subscribed to rather than awaited, so a three-minute describe does not hold a
    # request open and a page refresh re-attaches instead of losing the progress.
    return sse(request.app.state.jobs.watch())


# --- what was built ----------------------------------------------------------


@router.delete("")
async def reset(request: Request) -> dict:
    # Discards what the stages recorded and the tables they cached, for when the connection now
    # points somewhere else. The served model is untouched: it keeps answering until a rebuild
    # replaces it, which is why the next stage after this is `read`.
    await find()
    state.idle(request.app)
    await connection.reset_progress()
    return await read(request)


@router.post("/{stage}")
async def run_stage(stage: str, body: RunStage, request: Request) -> dict:
    if stage not in STAGES:
        raise HTTPException(404, f"no such stage {stage!r} — one of {', '.join(STAGES)}")
    # Bound out here so the closure does not hold the finished request — and with it the whole
    # ASGI scope — alive for the minutes a describe takes.
    app = request.app
    runner = app.state.jobs

    async def work(job: job_runner.Job) -> None:
        job.current = stage
        runner.emit("step_start", step=stage)
        try:
            result = await modelgen.steps.action(
                stage,
                schema=body.schema_name,
                tables=body.tables,
                report=lambda name, payload: runner.emit("progress", step=name, **payload),
            )
        except StepFailed as exc:
            # Cube's own refusal is in the message; there are no container logs to add, since
            # the deployment runs wherever its owner put it.
            runner.emit("step_end", step=stage, ok=False, error=str(exc))
            raise
        finally:
            # A rebuild changes what Cube serves and nothing watches it for that.
            state.reloaded(app)
            job.current = ""
        runner.emit(
            "step_end",
            step=stage,
            ok=result.ok,
            summary=result.summary,
            notes=result.notes,
            errors=result.errors,
        )

    try:
        job = await runner.start([stage], work)
    except job_runner.Busy as exc:
        raise HTTPException(409, f"a job is already running — {exc}") from None
    return job.snapshot()
