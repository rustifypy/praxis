"""Connection testing via Cube's own /readyz."""

import asyncio
from dataclasses import dataclass

import httpx

from ..cube.client import ADMIN_TIMEOUT_S, CubeError, catalog_of
from .registry import Source


@dataclass
class Health:
    ok: bool
    status: str
    detail: str = ""

    def __str__(self) -> str:
        return self.status if not self.detail else f"{self.status} — {self.detail}"

    def as_dict(self) -> dict:
        """For the wire. Structured, so the browser colours a dot from a boolean rather
        than by matching on wording that could change."""
        return {"healthy": self.ok, "status": self.status, "detail": self.detail}


async def check(source: Source, timeout: float = 20.0) -> Health:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{source.url}/readyz")
    except httpx.TimeoutException:
        return Health(False, "timeout", f"no response in {timeout:.0f}s")
    except httpx.ConnectError:
        return Health(False, "unreachable", f"nothing listening on port {source.port}")
    except httpx.RequestError as exc:
        # A booting container accepts the connection then resets it — ReadError, not ConnectError.
        return Health(False, "unreachable", f"{type(exc).__name__} on port {source.port}")

    try:
        body = r.json()
    except ValueError:
        body = {}
    status = str(body.get("health") or r.status_code)
    if r.status_code == 200:
        return Health(True, status)

    detail = next(
        (str(body[k])[:300] for k in ("error", "message", "detail") if body.get(k)),
        "Cube reports the data source connection is down",
    )
    return Health(False, status, detail)


@dataclass
class Serving:
    """What a source is exposing right now, from Cube's own `/v1/meta`."""

    cubes: int
    views: list[str]
    described: int


SERVING_STATES = ("stopped", "unreachable", "broken", "empty", "ok")


def serving_state(running: bool, healthy: bool, served: "Serving | None") -> str:
    """Why `serving` is what it is. It was `None` for two unrelated reasons — a stopped
    container and a model Cube refused to compile — and the sources list rendered the second as
    "no model yet", which is a wrong statement about a source with thirteen cubes rather than a
    vague one. Structured for the same reason `Health.as_dict` is: so nothing matches on prose.
    """
    if not running:
        return "stopped"
    if not healthy:
        return "unreachable"
    if served is None:
        return "broken"
    return "ok" if served.cubes else "empty"


async def serving(source: Source, timeout: float = ADMIN_TIMEOUT_S) -> Serving | None:
    """The compiled entities, or None when Cube cannot say. Counted over cubes only — a view's
    members are the same members re-exported, so counting every entity counts the model twice."""
    try:
        catalog = await catalog_of(source, timeout=timeout)
    except CubeError:
        return None
    return Serving(
        cubes=sum(1 for e in catalog.entities if not e.is_view),
        views=[e.name for e in catalog.views],
        described=sum(1 for e in catalog.entities if e.description),
    )


async def wait_until_ready(
    source: Source, attempts: int = 12, every: float = 5.0, down_tolerance: int = 2
) -> tuple[Health, int]:
    """Poll while the container boots. Returns the last health and attempts used."""
    health = Health(False, "unreachable")
    downs = 0
    for attempt in range(1, attempts + 1):
        health = await check(source)
        if health.ok:
            return health, attempt
        if health.status == "DOWN":
            downs += 1
            if downs > down_tolerance:
                return health, attempt
        await asyncio.sleep(every)
    return health, attempts
