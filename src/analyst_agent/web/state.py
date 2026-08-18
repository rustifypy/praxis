"""What every source route needs: the stores on `app.state`, and the source as the browser
sees it.

Shared rather than private because three routers build the same body — `model_edit` was
already importing `_find` through the underscore, which is what says a name is not private.
"""

import time

from fastapi import HTTPException, Request

from ..core import jobs
from ..sources import docker, health, vcs
from ..sources.registry import Source, model_dir
from ..sources.store import SourceStore, public

HEALTH_TTL_S = 15.0
"""How long a /readyz result is reused. Each probe is an HTTP call, and a status dot that
lies is worse than one that is a few seconds stale."""


async def init_state(app) -> None:
    """Called from the server's lifespan: open the store and the job runner."""
    store = SourceStore()
    await store.init()
    app.state.sources = store
    app.state.jobs = jobs.Runner()
    # (monotonic time, probe), or None. See HEALTH_TTL_S.
    app.state.health = None


# --- reading -----------------------------------------------------------------


async def probe(app, running: bool) -> dict:
    """Cached health and served entities: is it up, and what is it actually serving?"""
    if not running:
        return {
            "health": health.Health(False, "stopped").as_dict(),
            "serving": None,
            "serving_state": health.serving_state(False, False, None),
        }
    hit = app.state.health
    now = time.monotonic()
    if hit and now - hit[0] < HEALTH_TTL_S:
        return hit[1]

    result = await health.check(timeout=5.0)
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


async def view(
    request: Request,
    source: Source,
    container_state: str,
    steps: dict,
    catalog_read: bool | None = None,
) -> dict:
    running = container_state == "running"
    # Looked up unless the caller already has it, never defaulted: the stepper renders the
    # shipped rail, so a wrong answer here erases a tick rather than being merely imprecise.
    if catalog_read is None:
        catalog_read = await request.app.state.sources.catalog_read()
    probed = await probe(request.app, running)
    body = public(
        source, steps=steps, running=running, health=probed["health"], catalog_read=catalog_read
    )
    body["serving"] = probed["serving"]
    body["serving_state"] = probed["serving_state"]
    body["container_state"] = container_state or "missing"
    # The checked-out branch is the model being served, so anywhere a query can be issued has
    # to be able to say which one. Empty until the directory is a repo.
    repo = model_dir()
    body["branch"] = await vcs.current(repo) if vcs.is_repo(repo) else ""
    return body


async def read(request: Request) -> dict:
    """The whole source body, container state included — the answer every write route ends on."""
    source = await find(request)
    steps = await request.app.state.sources.steps()
    return await view(request, source, await docker.state(), steps)


# --- shared ------------------------------------------------------------------


async def find(request: Request) -> Source:
    source = await request.app.state.sources.get()
    if source is None:
        raise HTTPException(404, "no database is connected yet")
    return source
