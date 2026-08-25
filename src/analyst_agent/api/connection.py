"""The one connection, and the deployment that serves it.

Two nouns, one module, because connecting is two steps and the second one is the deployment:
`POST /connection` stores credentials and mints secrets, someone runs the compose files, and
`PUT /deployment` hands the addresses back.
"""

import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..core import deployenv
from ..core import deployment as connection
from ..core.deployment import Deployment
from ..modelstore import StoreError, definitions, model_files
from ..modelstore.client import ADMIN_TIMEOUT_S, served_catalog
from ..modelstore.modelgen import evidence
from . import reachable
from .support import state

router = APIRouter(prefix="/api")

COMPOSE_FILE = "docker-compose.yml"

HEALTH_TTL_S = 15.0

ENV_PREFIXES = ("CUBEJS_DB_", "CUBEJS_AWS_")

RENDERERS = {"semantic": deployenv.semantic, "sandbox": deployenv.sandbox}


# --- what Cube is serving ------------------------------------------------------
#
# Here rather than in `modelstore/`: `STATES` is what the browser's copy is keyed on, so these
# are the words a chip reads and this route is the only thing that asks for them.


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


class NewConnection(BaseModel):
    description: str = ""
    driver: str
    values: dict[str, str] = Field(default_factory=dict)


class EditConnection(BaseModel):
    description: str | None = None
    values: dict[str, str] = Field(default_factory=dict)


class Addresses(BaseModel):
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
        if not name.startswith(ENV_PREFIXES):
            raise HTTPException(400, f"{key!r} is not a connection setting")
        if value.strip():
            envs[name] = value.strip()
    return envs


@router.post("/connection")
async def connect(body: NewConnection, request: Request) -> dict:
    # Nothing is running after this: the addresses arrive later from whoever ran the compose
    # file, which is the whole reason connecting is two steps.
    state.idle(request.app)
    driver = body.driver.strip()
    if not driver:
        raise HTTPException(400, "pick the kind of database this is")

    credentials = {**_envs(body.values), "CUBEJS_DB_TYPE": driver}
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
    return await view(request, built, steps={})


@router.patch("/connection")
async def edit(body: EditConnection, request: Request) -> dict:
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
