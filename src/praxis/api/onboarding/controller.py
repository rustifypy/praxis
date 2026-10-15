"""Onboarding, from the credentials to a model: what is connected, what answers, and
running one stage as a job.

`start` is the only one that touches `app`, and it has to: a stage is a background job, so the
thing it reports progress to outlives the request that asked for it. It raises `jobs.Busy` rather
than a 409 — one job at a time is a fact about the runner, and which status code that is belongs
to the route.
"""

import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from ...core import deployenv
from ...core import deployment as connection
from ...core import jobs as job_runner
from ...core.deployment import Deployment
from ...modelstore import StoreError, definitions, model_files, modelgen
from ...modelstore.client import ADMIN_TIMEOUT_S, served_catalog
from ...modelstore.modelgen import evidence, selection, steps
from ...modelstore.modelgen.steps import StepFailed
from ..support import state
from . import reachable
from .dto import Cancelled, Closure, JobSnapshot, SchemaTables, TableCatalog, TableRow

COMPOSE_FILE = "docker-compose.yml"

HEALTH_TTL_S = 15.0

ENV_PREFIXES = ("CUBEJS_DB_", "CUBEJS_AWS_")

RENDERERS = {"semantic": deployenv.semantic, "sandbox": deployenv.sandbox}


async def cached_tables() -> TableCatalog:
    """What the `read` stage cached. **Never touches the database** — this is read on every page
    build and once per checkbox tick."""
    carried = await connection.state()
    schemas = await steps.cached_introspection(state=carried)
    return TableCatalog(
        read=bool(schemas),
        truncated=bool(carried.get("introspection_truncated")),
        notes=carried.get("introspection_notes") or [],
        selection=carried.get("selection") or {},
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
    found = await steps.cached_introspection(schema_name)
    if schema_name not in found:
        return None
    schema = found[schema_name]
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


# --- what is connected, and what is answering --------------------------------


async def find() -> Deployment:
    found = await connection.current()
    if found is None:
        raise HTTPException(404, "no database is connected yet")
    return found


async def probe(app, deployment: Deployment) -> dict:
    hit = app.state.health
    now = time.monotonic()
    if hit and now - hit[0] < HEALTH_TTL_S:
        return hit[1]

    found = await reachable.alive(deployment)
    if not deployment.deployed:
        probed = {
            "health": reachable.Reached(False, "not deployed").as_dict(),
            "serving": None,
            "serving_state": serving_state(False, False, None),
        }
    else:
        result = await reachable.querying(deployment)
        served = await read_serving() if result.ok else None
        probed = {
            "health": result.as_dict(),
            "serving": served and served.__dict__,
            "serving_state": serving_state(True, result.ok, served),
        }
    probed["sandbox_reason"] = "" if found.ok else found.detail
    app.state.health = (now, probed)
    return probed


async def view(request: Request, deployment: Deployment, steps: dict) -> dict:
    probed = await probe(request.app, deployment)
    store_error = ""
    try:
        files = await model_files()
    except StoreError as exc:
        files = {}
        store_error = str(exc)
    found_files, _ = definitions.read(files)
    found = evidence.of(found_files, await connection.state())
    return {
        **public(deployment, steps=steps, health=probed["health"], found=found),
        "store_error": store_error,
        "serving": probed["serving"],
        "serving_state": probed["serving_state"],
        "sandbox_reason": probed["sandbox_reason"],
    }


async def read(request: Request) -> dict:
    """The whole body, which is the answer every write route here and in `pipeline` ends on."""
    deployment = await find()
    return await view(request, deployment, await connection.steps())


# --- the connection ----------------------------------------------------------


def envs(values: dict[str, str]) -> dict[str, str]:
    # A blank value is dropped, which is what makes it mean "keep what is stored" on an edit:
    # the form is never sent the password it would otherwise overwrite.
    envs = {}
    for key, value in values.items():
        name = key.strip().upper()
        if not name.startswith(ENV_PREFIXES):
            raise HTTPException(400, f"{key!r} is not a connection setting")
        if value.strip():
            envs[name] = value.strip()
    return envs


# --- what Cube is serving ----------------------------------------------------
#
# `STATES` is what the browser's copy is keyed on, so these are the words a chip reads.


@dataclass
class Serving:
    cubes: int
    views: list[str]
    described: int


STATES = ("not deployed", "unreachable", "broken", "empty", "ok")


def serving_state(deployed: bool, ok: bool, served: "Serving | None") -> str:
    # `deployed` and not `running`: the portal starts nothing, so it can never observe a stop.
    if not deployed:
        return "not deployed"
    if not ok:
        return "unreachable"
    if served is None:
        return "broken"
    return "ok" if served.cubes else "empty"


async def read_serving(timeout: float = ADMIN_TIMEOUT_S) -> Serving | None:
    # Counted over cubes only: a view's members are the same members re-exported, so counting
    # every entity counts the model twice.
    try:
        catalog = await served_catalog(timeout=timeout)
        return Serving(
            cubes=sum(1 for entity in catalog.entities if not entity.is_view),
            views=[entity.name for entity in catalog.views],
            described=sum(1 for entity in catalog.entities if entity.description),
        )
    except StoreError:
        return None


def public(
    deployment: Deployment,
    steps: dict,
    health: dict,
    found: evidence.Evidence,
) -> dict:
    # Every field except the secrets.
    return {
        "label": deployment.label,
        "description": deployment.description,
        "driver": deployment.driver,
        "store_url": deployment.store_url,
        # Not part of `deployed`: a deployment without a sandbox answers questions perfectly
        # well and just cannot run Python.
        "sandbox_url": deployment.sandbox_url,
        "deployed": deployment.deployed,
        "onboarded": evidence.onboarded(deployment, found),
        # Names only, never values, so the edit form can show which fields are set.
        "credential_keys": sorted(deployment.credentials),
        "steps": steps,
        "health": health,
        "cubes": list(found.cubes),
        "views": list(found.views),
    }
