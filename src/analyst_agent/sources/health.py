"""Connection testing via Cube's own /readyz."""

import asyncio
from dataclasses import dataclass

import httpx

from ..core.config import settings
from ..cube.client import ADMIN_TIMEOUT_S, CubeError, served_catalog


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


async def check(timeout: float = 20.0) -> Health:
    where = settings.cube_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{where}/readyz")
    except httpx.TimeoutException:
        return Health(False, "timeout", f"no response in {timeout:.0f}s")
    except httpx.ConnectError:
        return Health(False, "unreachable", f"nothing listening on {where}")
    except httpx.RequestError as exc:
        # A booting container accepts the connection then resets it — ReadError, not ConnectError.
        return Health(False, "unreachable", f"{type(exc).__name__} on {where}")

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
    """What Cube is exposing right now, from its own `/v1/meta`."""

    cubes: int
    views: list[str]
    described: int


SERVING_STATES = ("stopped", "unreachable", "broken", "empty", "ok")


def serving_state(running: bool, healthy: bool, served: "Serving | None") -> str:
    """Why `serving` is what it is. A null `Serving` has two unrelated causes — a stopped
    container, and a model Cube refuses to compile — and only `broken` may be reported as
    anything other than "no model yet". Structured like `Health.as_dict`, so nothing infers it.
    """
    if not running:
        return "stopped"
    if not healthy:
        return "unreachable"
    if served is None:
        return "broken"
    return "ok" if served.cubes else "empty"


async def serving(timeout: float = ADMIN_TIMEOUT_S) -> Serving | None:
    """The compiled entities, or None when Cube cannot say. Counted over cubes only — a view's
    members are the same members re-exported, so counting every entity counts the model twice."""
    try:
        catalog = await served_catalog(timeout=timeout)
    except CubeError:
        return None
    return Serving(
        cubes=sum(1 for e in catalog.entities if not e.is_view),
        views=[e.name for e in catalog.views],
        described=sum(1 for e in catalog.entities if e.description),
    )


async def wait_until_ready(
    attempts: int = 12, every: float = 5.0, down_tolerance: int = 2
) -> tuple[Health, int]:
    """Poll while the container boots. Returns the last health and attempts used."""
    health = Health(False, "unreachable")
    downs = 0
    for attempt in range(1, attempts + 1):
        health = await check()
        if health.ok:
            return health, attempt
        if health.status == "DOWN":
            downs += 1
            if downs > down_tolerance:
                return health, attempt
        await asyncio.sleep(every)
    return health, attempts
