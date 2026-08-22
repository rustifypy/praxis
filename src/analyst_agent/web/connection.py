"""The one connection, and the deployment that serves it.

Two nouns, one module, because connecting is two steps and the second one is the deployment:
`POST /connection` stores credentials and mints secrets, someone runs the compose files, and
`PUT /deployment` hands the addresses back.
"""

import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import reachable
from ..core import deployment as connection
from ..core.deployment import Deployment
from ..datamodel import definitions, deployenv, evidence, serving
from ..modelstore import StoreError, model_files
from . import state

router = APIRouter(prefix="/api")

COMPOSE_FILE = "docker-compose.yml"

# How long a probe is reused: each one is an HTTP call, and a dot that is a few seconds stale
# beats one that costs a round trip per page build.
HEALTH_TTL_S = 15.0

# The one rule about a connection variable's name. Everything the compose file sets itself is
# CUBEJS_ without the DB, so a typed key can never overwrite the API secret or dev mode.
ENV_PREFIX = "CUBEJS_DB_"

# The two deployments, and nothing else is one. Named here so the route cannot be asked for a
# third, and so the browser's two download links and this dict cannot disagree on the names.
RENDERERS = {"semantic": deployenv.semantic, "sandbox": deployenv.sandbox}


class NewConnection(BaseModel):
    description: str = ""
    driver: str
    # Whatever CUBEJS_DB_* variables this warehouse needs. No per-driver field list: the portal
    # writes what it is given into the compose file, and Cube is what reads them.
    values: dict[str, str] = Field(default_factory=dict)


class EditConnection(BaseModel):
    description: str | None = None
    # A key with an empty value keeps what is stored: the form is never sent the password it
    # would otherwise overwrite with blank.
    values: dict[str, str] = Field(default_factory=dict)


class Addresses(BaseModel):
    # Either or both. Step two of connecting, once someone has run the compose files.
    store_url: str = ""
    sandbox_url: str = ""


# --- the body every route here answers with ----------------------------------


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

    # Liveness and identity but not the token, since this answers on every page build. A field
    # rather than a failed request: nothing but `run_python` needs a sandbox.
    found = await reachable.alive(deployment)
    if not deployment.deployed:
        # Not "down": nothing has been run yet, and calling that unhealthy would put a red dot
        # on a deployment whose compose file has not been handed over.
        probed = {
            "health": reachable.Reached(False, "not deployed").as_dict(),
            "serving": None,
            "serving_state": serving.state(False, False, None),
        }
    else:
        result = await reachable.querying(deployment)
        served = await serving.read() if result.ok else None
        probed = {
            "health": result.as_dict(),
            "serving": served and served.__dict__,
            # Cached with the probe: it is a fact about this probe rather than about the row,
            # and the browser must not re-derive it from `serving` being null.
            "serving_state": serving.state(True, result.ok, served),
        }
    probed["sandbox_reason"] = "" if found.ok else found.detail
    app.state.health = (now, probed)
    return probed


async def view(request: Request, deployment: Deployment, steps: dict) -> dict:
    probed = await probe(request.app, deployment)
    served = (probed["serving"] or {}).get("cubes", 0)
    # A store that is down must not take this page with it: saying what is broken is the page's
    # whole job. Everywhere else `model_files` raises, deliberately.
    store_error = ""
    try:
        files = await model_files()
    except StoreError as exc:
        files = {}
        store_error = str(exc)
    found_files, _ = definitions.read(files)
    found = evidence.of(found_files, await connection.state(), served=served)
    return {
        **evidence.public(deployment, steps=steps, health=probed["health"], found=found),
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


@router.get("/connection")
async def get_connection(request: Request) -> dict | None:
    found = await connection.current()
    if found is None:
        return None
    return await view(request, found, await connection.steps())


def _envs(values: dict[str, str]) -> dict[str, str]:
    # A blank value is dropped, which is what makes it mean "keep what is stored" on an edit:
    # the form is never sent the password it would otherwise overwrite.
    envs = {}
    for key, value in values.items():
        name = key.strip().upper()
        if not name.startswith(ENV_PREFIX):
            raise HTTPException(400, f"{key!r} is not a {ENV_PREFIX}* variable")
        if value.strip():
            envs[name] = value.strip()
    return envs


@router.post("/connection")
async def connect(body: NewConnection, request: Request) -> dict:
    # Nothing is running after this: the addresses arrive later from whoever ran the compose
    # file, which is the whole reason connecting is two steps.
    state.idle(request.app)
    if not body.driver.strip():
        raise HTTPException(400, "no warehouse type — it becomes CUBEJS_DB_TYPE")

    # Minted once: they go into the compose file, and the deployment that reads it has to keep
    # matching the row, so rotating one means re-running that deployment.
    built = Deployment(
        api_secret=secrets.token_urlsafe(36),
        store_token=secrets.token_urlsafe(32),
        store_read_token=secrets.token_urlsafe(32),
        sandbox_token=secrets.token_urlsafe(32),
        driver=body.driver.strip(),
        description=body.description,
        credentials={**_envs(body.values), "CUBEJS_DB_TYPE": body.driver.strip()},
    )
    await connection.save(built)
    state.reloaded(request.app)
    return await view(request, built, steps={})


@router.patch("/connection")
async def edit(body: EditConnection, request: Request) -> dict:
    # Credentials only. The addresses are `PUT /deployment`, which checks them first. Nothing is
    # invalidated here: a changed host may be the same database moved and a changed name may
    # expose the same tables, so which it was is not derivable from the values. `DELETE
    # /pipeline` is how what was built is discarded, deliberately.
    deployment = await find()
    merged = None
    if body.values:
        state.idle(request.app)
        merged = {**deployment.credentials, **_envs(body.values)}

    await connection.patch(description=body.description, credentials=merged)
    state.reloaded(request.app)
    return await read(request)


@router.post("/connection/probe")
async def probe_now(request: Request) -> dict:
    # Probe now, ignoring HEALTH_TTL_S.
    state.reloaded(request.app)
    return await read(request)


# --- the deployment ----------------------------------------------------------


@router.get("/deployment/{service}/compose")
async def compose(service: str) -> Response:
    # The one route that returns credential *values*: handing them over is its entire purpose,
    # since nothing can start the deployment without them. A download and never a body the page
    # renders — the only thing to do with the file is run it somewhere.
    try:
        if service not in RENDERERS:
            raise HTTPException(404, f"no such service {service!r} — one of {', '.join(RENDERERS)}")
        deployment = await find()
        text = RENDERERS[service](deployment)
        return Response(
            text,
            media_type="application/yaml",
            headers={"Content-Disposition": f'attachment; filename="{COMPOSE_FILE}"'},
        )
    except RuntimeError as exc:
        # Not a 500: a secret the deployment has none of is actionable, and a file with an
        # unresolved variable reads as complete and dies at `up`. `render` raises nothing else.
        raise HTTPException(503, str(exc)) from None


@router.put("/deployment")
async def deployed(body: Addresses, request: Request) -> dict:
    """Step two of connecting: what was run, and where.

    Each address is checked before it is stored, so one that does not answer never has to be
    undone. Whichever passed is saved and the rest is reported — the same call follows a
    deployment that moved.
    """
    deployment = await find()
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
        "connection": await read(request),
    }


@router.post("/deployment/secrets")
async def rotate(request: Request) -> dict:
    await find()
    state.idle(request.app)
    await connection.patch(
        api_secret=secrets.token_urlsafe(36),
        store_token=secrets.token_urlsafe(32),
        store_read_token=secrets.token_urlsafe(32),
        sandbox_token=secrets.token_urlsafe(32),
    )
    state.reloaded(request.app)
    return await read(request)
