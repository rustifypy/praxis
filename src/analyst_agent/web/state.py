"""What every deployment route needs: the stores on `app.state`, and the deployment as the
browser sees it.

Shared rather than private because three routers build the same body — `model_edit` was
already importing `_find` through the underscore, which is what says a name is not private.
"""

import time

from fastapi import HTTPException, Request

from ..core import jobs
from ..core.deployment import Deployment, DeploymentStore
from ..cube.client import CubeClient
from ..datamodel import definitions, evidence, health, vcs
from ..datamodel.registry import model_dir
from ..modelstore import StoreError, model_files

HEALTH_TTL_S = 15.0
"""How long a /readyz result is reused. Each probe is an HTTP call, and a status dot that
lies is worse than one that is a few seconds stale."""


async def init_state(app) -> None:
    """Called from the server's lifespan: open the store and the job runner."""
    store = DeploymentStore()
    await store.init()
    app.state.sources = store
    app.state.jobs = jobs.Runner()
    # (monotonic time, probe), or None. See HEALTH_TTL_S.
    app.state.health = None
    # Built on first use and rebuilt when the row's address or secret moves — see `cube`.
    app.state.cube = None
    app.state.cube_key = None


# --- reading -----------------------------------------------------------------


async def cube(app) -> CubeClient:
    """The session for the configured deployment. A held client carries an address, so an
    edited URL has to replace it rather than be ignored."""
    deployment = await DeploymentStore().get()
    if deployment is None or not deployment.store_url:
        raise HTTPException(503, "no Cube deployment is configured")
    key = (deployment.store_url, deployment.store_token)
    if app.state.cube_key != key:
        if app.state.cube is not None:
            await app.state.cube.aclose()
        app.state.cube = CubeClient.for_deployment(deployment)
        app.state.cube_key = key
    return app.state.cube


async def probe(app, deployment: Deployment) -> dict:
    """Cached health and served entities: is it answering, and what is it serving?"""
    if not deployment.deployed:
        # Not "down" — nothing has been run yet, and calling that unhealthy would put a red dot
        # on a deployment whose environment file has not been handed over.
        return {
            "health": health.Health(False, "not deployed").as_dict(),
            "serving": None,
            "serving_state": health.serving_state(False, False, None),
        }
    hit = app.state.health
    now = time.monotonic()
    if hit and now - hit[0] < HEALTH_TTL_S:
        return hit[1]

    result = await health.check(deployment, timeout=5.0)
    served = await health.serving() if result.ok else None
    probed = {
        "health": result.as_dict(),
        "serving": served and served.__dict__,
        # Cached with the probe, because it is a fact about this probe rather than about the
        # row — and the browser must not re-derive it from `serving` being null.
        "serving_state": health.serving_state(True, result.ok, served),
    }
    app.state.health = (now, probed)
    return probed


async def view(request: Request, deployment: Deployment, steps: dict) -> dict:
    probed = await probe(request.app, deployment)
    served = (probed["serving"] or {}).get("cubes", 0)
    # A store that is down must not take this page with it: the page's whole job is to say
    # what is broken, and an empty rail beside an explicit "the store is not answering" says
    # more than a 500 does. Everywhere else `model_files` raises, deliberately.
    store_error = ""
    try:
        files = await model_files()
    except StoreError as exc:
        files = {}
        store_error = str(exc)
    found_files, _ = definitions.read(files)
    found = evidence.of(found_files, await request.app.state.sources.state(), served=served)
    body = evidence.public(deployment, steps=steps, health=probed["health"], found=found)
    body["store_error"] = store_error
    body["serving"] = probed["serving"]
    body["serving_state"] = probed["serving_state"]
    # The checked-out branch is the model being served, so anywhere a query can be issued has
    # to be able to say which one. Empty until the directory is a repo.
    repo = model_dir()
    body["branch"] = await vcs.current(repo) if vcs.is_repo(repo) else ""
    return body


async def read(request: Request) -> dict:
    """The whole deployment body — the answer every write route ends on."""
    deployment = await find(request)
    return await view(request, deployment, await request.app.state.sources.steps())


# --- shared ------------------------------------------------------------------


async def find(request: Request) -> Deployment:
    deployment = await request.app.state.sources.get()
    if deployment is None:
        raise HTTPException(404, "no database is connected yet")
    return deployment
