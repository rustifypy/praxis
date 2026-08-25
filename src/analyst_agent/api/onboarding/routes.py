"""The twelve onboarding routes, in the order the work happens.

Connecting is **two steps**, and the portal cannot do the middle one: `POST /connection` stores
credentials and mints secrets with no addresses, someone runs the compose file the hand-over
renders, and `PUT /deployment` receives the addresses and checks each before storing. Building the
model is what finishes it.

Two prefixes, because the nouns are two: `/api/connection` and `/api/deployment` are the
deployment, `/api/pipeline` is the model being built out of it.

**Order is load-bearing on the pipeline router.** `POST /{stage}` is as broad as a path parameter
gets, so it must stay below `/tables` and `/job` — registered first it would answer for both.
`test_surface.py` asserts that; it still has to read as deliberate.
"""

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from ...core import deployenv
from ...core import deployment as connection
from ...core import jobs as job_runner
from ...core.deployment import Deployment
from ...modelstore.modelgen.evidence import STAGES
from ..support import state
from ..support.stream import sse
from . import controller, reachable
from .controller import COMPOSE_FILE, RENDERERS
from .dto import (
    Addresses,
    Cancelled,
    Closure,
    EditConnection,
    JobSnapshot,
    NewConnection,
    StageRequest,
    TableCatalog,
)

router = APIRouter(prefix="/api")

# The model being built out of the connection. Its own router so `POST /{stage}` cannot reach
# past `/pipeline`, where a single `/api` prefix would have let it answer for `/connection`.
pipeline = APIRouter(prefix="/api/pipeline")


@router.get("/connection")
async def get_connection(request: Request) -> dict | None:
    found = await connection.current()
    if found is None:
        return None
    return await controller.view(request, found, await connection.steps())


@router.post("/connection")
async def connect(body: NewConnection, request: Request) -> dict:
    # Nothing is running after this: the addresses arrive later from whoever ran the compose
    # file, which is the whole reason connecting is two steps.
    state.idle(request.app)
    driver = body.driver.strip()
    if not driver:
        raise HTTPException(400, "pick the kind of database this is")

    credentials = {**controller.envs(body.values), "CUBEJS_DB_TYPE": driver}
    if owed := deployenv.missing(driver, credentials):
        raise HTTPException(400, f"still needs {', '.join(owed)}")

    built = Deployment(
        api_secret=secrets.token_urlsafe(36),
        store_token=secrets.token_urlsafe(32),
        sandbox_token=secrets.token_urlsafe(32),
        driver=driver,
        description=body.description,
        credentials=credentials,
    )
    await connection.save(built)
    state.reloaded(request.app)
    return await controller.view(request, built, steps={})


@router.patch("/connection")
async def edit(body: EditConnection, request: Request) -> dict:
    deployment = await controller.find()
    merged = None
    if body.values:
        state.idle(request.app)
        merged = {**deployment.credentials, **controller.envs(body.values)}

    await connection.patch(description=body.description, credentials=merged)
    state.reloaded(request.app)
    return await controller.read(request)


@router.post("/connection/probe")
async def probe_now(request: Request) -> dict:
    # Probe now, ignoring HEALTH_TTL_S.
    state.reloaded(request.app)
    return await controller.read(request)


# --- the deployment ----------------------------------------------------------


@router.get("/deployment/{service}/compose")
async def compose(service: str) -> Response:
    try:
        if service not in RENDERERS:
            raise HTTPException(404, f"no such service {service!r} — one of {', '.join(RENDERERS)}")
        deployment = await controller.find()
        text = RENDERERS[service](deployment)
        return Response(
            text,
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="{COMPOSE_FILE}"'},
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None


@router.put("/deployment")
async def deployed(body: Addresses, request: Request) -> dict:
    """Step two of connecting: what was run, and where.

    Each address is checked before it is stored, so one that does not answer never has to be
    undone. Whichever passed is saved and the rest is reported — the same call follows a
    deployment that moved.
    """
    deployment = await controller.find()
    checked = {}
    for service, url in (("semantic", body.store_url), ("sandbox", body.sandbox_url)):
        if url.strip():
            checked[service] = await reachable.test(deployment, service, url.strip())

    passed = {service: result.ok for service, result in checked.items()}
    await connection.patch(
        store_url=body.store_url.strip() if passed.get("semantic") else None,
        sandbox_url=body.sandbox_url.strip() if passed.get("sandbox") else None,
    )
    state.reloaded(request.app)
    return {
        "checked": {service: result.as_dict() for service, result in checked.items()},
        "connection": await controller.read(request),
    }


# --- building the model ------------------------------------------------------


@pipeline.get("/tables", response_model=TableCatalog)
async def read_tables() -> TableCatalog:
    """The warehouse's tables as the `read` stage cached them, one row per table for the picker.
    Never opens the database: this is answered on every page build and on every checkbox tick."""
    return await controller.cached_tables()


@pipeline.post("/tables/closure", response_model=Closure)
async def read_closure(body: StageRequest) -> Closure:
    """The tables a selection drags in through its joins, plus what it cannot join at all.
    404 when that schema has not been read — there is nothing cached to close over."""
    found = await controller.close_over(body.schema_name, body.tables)
    if found is None:
        raise HTTPException(404, f"schema {body.schema_name!r} has not been read")
    return found


@pipeline.get("/job", response_model=JobSnapshot)
async def read_job(request: Request) -> JobSnapshot:
    """The running job and its buffered events, so a page refresh re-attaches to the narration.
    Answers `{"id": null}` and never a 404 when nothing is running: nothing is wrong."""
    return controller.running(request.app.state.jobs)


@pipeline.delete("/job", response_model=Cancelled)
async def cancel_job(request: Request) -> Cancelled:
    """Ask the running job to stop, and say whether there was one.
    False is a normal answer, not an error — the button can always land after the job ended."""
    return controller.cancel(request.app.state.jobs)


@pipeline.get("/job/stream")
async def stream_job(request: Request) -> StreamingResponse:
    """The running job's narration as server-sent events, replayed from its buffer on connect.
    No `response_model`: a job is subscribed to rather than awaited, so a three-minute describe
    does not hold a request open."""
    return sse(request.app.state.jobs.watch())


# Below every fixed-segment route above it. See the module docstring.
@pipeline.post("/{stage}", response_model=JobSnapshot)
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
