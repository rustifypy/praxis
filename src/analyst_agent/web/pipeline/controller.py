"""Reading the cached catalog, closing over a selection, and running one stage as a job.

`start` is the only one that touches `app`, and it has to: a stage is a background job, so the
thing it reports progress to outlives the request that asked for it. It raises `jobs.Busy` rather
than a 409 — one job at a time is a fact about the runner, and which status code that is belongs
to the route.
"""

from ...core import jobs as job_runner
from ...modelstore import modelgen
from ...modelstore.modelgen import selection
from ...modelstore.modelgen.steps import StepFailed
from ..support import state
from .dto import Cancelled, Closure, JobSnapshot, SchemaTables, TableCatalog, TableRow


async def cached_tables() -> TableCatalog:
    """What the `read` stage cached. **Never touches the database** — this is read on every page
    build and once per checkbox tick."""
    from ...core import deployment as connection

    current = await connection.state()
    schemas = {
        name: modelgen.warehouse.load_schema(raw)
        for name, raw in (current.get("catalog") or {}).items()
    }
    return TableCatalog(
        read=bool(schemas),
        truncated=bool(current.get("catalog_truncated")),
        notes=current.get("catalog_notes") or [],
        selection=current.get("selection") or {},
        schemas=[
            SchemaTables(
                schema_name=name,
                tables=[TableRow(**row) for row in selection.describe(schema)],
            )
            for name, schema in sorted(schemas.items())
        ],
    )


async def close_over(schema_name: str, tables: list[str] | None) -> Closure | None:
    """What a selection drags in, or `None` when that schema has not been read.

    Only the requested schema is rebuilt. This fires on every checkbox tick, and rebuilding every
    schema would reconstruct every `Column` of each one each time.
    """
    from ...core import deployment as connection

    raw = (await connection.state()).get("catalog") or {}
    if schema_name not in raw:
        return None
    schema = modelgen.warehouse.load_schema(raw[schema_name])
    picked = tables or []
    chosen = sorted(selection.closure(schema, picked))
    return Closure(
        tables=chosen,
        added=[table for table in chosen if table not in picked],
        cut_joins=selection.cut_joins(schema, chosen),
        keyless=[table for table in selection.keyless(schema) if table in chosen],
    )


def running(runner: job_runner.Runner) -> JobSnapshot:
    """The job in flight and its buffered narration, or an empty snapshot."""
    return JobSnapshot(**runner.job.snapshot()) if runner.job else JobSnapshot()


def cancel(runner: job_runner.Runner) -> Cancelled:
    """Ask the running job to stop. False when there was nothing to stop, which is not an error —
    the button can always be late."""
    return Cancelled(cancelled=runner.cancel())


async def start(app, stage: str, schema_name: str, tables: list[str] | None) -> JobSnapshot:
    """Run one stage as a job, answering as soon as the job exists.

    Raises `jobs.Busy` when one is already running. Does **not** validate the stage name: which
    stages exist is `evidence.STAGES`, and refusing an unknown one is a status code.

    `app` and `runner` are bound out here rather than read off the request inside the closure, so
    a finished request — and with it the whole ASGI scope — is not held alive for the minutes a
    describe takes.
    """
    runner = app.state.jobs

    async def work(job: job_runner.Job) -> None:
        job.current = stage
        runner.emit("step_start", step=stage)
        try:
            result = await modelgen.steps.action(
                stage,
                schema=schema_name,
                tables=tables,
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

    job = await runner.start([stage], work)
    return JobSnapshot(**job.snapshot())
