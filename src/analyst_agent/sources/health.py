"""Connection testing via Cube's own /readyz.

Cube runs the driver's connection test behind /readyz, so a source is verified
by the same code path that will serve queries — no second database client here.
"""

import asyncio
from dataclasses import dataclass

import httpx

from ..config import settings
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
    """What a source is exposing right now, from Cube's own `/v1/meta`.

    A fact about the source, alongside its health — not a rendering concern. It is also
    the only honest answer to "is there a model": the step records say what this portal
    built, and a model can predate the portal or be edited by hand.
    """

    cubes: int
    views: list[str]
    described: int


async def serving(source: Source, timeout: float = 10.0) -> Serving | None:
    """The compiled entities, or None when Cube cannot say.

    None rather than an error: a compile failure means `/v1/meta` has no cubes, and the
    source is still up — that is a model problem, not a health problem.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            body = (await client.get(f"{source.url}{settings.cube_base_path}/v1/meta")).json()
        entities = body.get("cubes") or []
        return Serving(
            cubes=sum(1 for e in entities if e.get("type") != "view"),
            views=[e["name"] for e in entities if e.get("type") == "view"],
            described=sum(1 for e in entities if e.get("description")),
        )
    except (httpx.RequestError, ValueError, KeyError, TypeError):
        return None


async def wait_until_ready(
    source: Source, attempts: int = 12, every: float = 5.0, down_tolerance: int = 2
) -> tuple[Health, int]:
    """Poll while the container boots. Returns the last health and attempts used.

    A DOWN is retried `down_tolerance` times — a warehouse can still be waking up —
    then reported, so bad credentials fail in seconds rather than a full timeout.
    """
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
