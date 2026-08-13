"""What every sources route needs: the stores on `app.state`, and one source as the browser
sees it.

Shared rather than private because three routers build the same body — `model_edit` was
already importing `_find` through the underscore, which is what says a name is not private.
"""

import time

from fastapi import HTTPException, Request

from ..sources import health, jobs
from ..sources.model import vcs
from ..sources.registry import Source
from ..sources.store import SourceStore, public

HEALTH_TTL_S = 15.0
"""How long a /readyz result is reused. Each probe is an HTTP call per source, and a
status dot that lies is worse than one that is a few seconds stale."""


async def init_state(app) -> None:
    """Called from the server's lifespan: open the store and the job runner."""
    store = SourceStore()
    await store.init()
    app.state.sources = store
    app.state.jobs = jobs.Runner()
    # name -> (monotonic time, probe). See HEALTH_TTL_S.
    app.state.health = {}


def store_of(request: Request) -> SourceStore:
    return request.app.state.sources


def runner_of(request: Request) -> jobs.Runner:
    return request.app.state.jobs


def invalidate(cache: dict, source: Source) -> None:
    """Forget the health probe for a source whose model just changed — nothing watches a
    container for changes. The served catalog needs no hook: it is cached on the Cube session
    for `CATALOG_TTL_S` and ages out on its own."""
    cache.pop(source.name, None)


# --- reading -----------------------------------------------------------------


async def probe(cache: dict, source: Source, running: bool) -> dict:
    """Cached health and served entities: is it up, and what is it actually serving?"""
    if not running:
        return {
            "health": health.Health(False, "stopped").as_dict(),
            "serving": None,
            "serving_state": health.serving_state(False, False, None),
        }
    hit = cache.get(source.name)
    now = time.monotonic()
    if hit and now - hit[0] < HEALTH_TTL_S:
        return hit[1]

    result = await health.check(source, timeout=5.0)
    served = await health.serving(source) if result.ok else None
    probed = {
        "health": result.as_dict(),
        "serving": served and served.__dict__,
        # Cached with the probe, because it is a fact about this probe rather than about the
        # row — and the browser must not re-derive it from `serving` being null.
        "serving_state": health.serving_state(True, result.ok, served),
    }
    cache[source.name] = (now, probed)
    return probed


async def view(
    request: Request,
    source: Source,
    containers: dict[str, str],
    steps: dict,
    catalog_read: bool | None = None,
) -> dict:
    state = containers.get(source.service, "")
    running = state == "running"
    # Looked up unless the caller already has it, never defaulted: the stepper renders the
    # shipped rail, so a wrong answer here erases a tick rather than being merely imprecise.
    if catalog_read is None:
        catalog_read = await store_of(request).catalog_read(source.name)
    probed = await probe(request.app.state.health, source, running)
    body = public(
        source, steps=steps, running=running, health=probed["health"], catalog_read=catalog_read
    )
    body["serving"] = probed["serving"]
    body["serving_state"] = probed["serving_state"]
    body["container_state"] = state or "missing"
    # The checked-out branch is the model this source serves, so anywhere a query can be
    # issued has to be able to say which one. Empty until the directory is a repo.
    model_dir = source.model_dir()
    body["branch"] = await vcs.current(model_dir) if vcs.is_repo(model_dir) else ""
    return body


# --- shared ------------------------------------------------------------------


async def find(request: Request, name: str) -> Source:
    source = await store_of(request).get(name)
    if source is None:
        raise HTTPException(404, f"no source named {name!r}")
    return source
