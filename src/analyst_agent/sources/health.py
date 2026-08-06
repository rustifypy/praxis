"""Connection testing via Cube's own /readyz.

Cube runs the driver's connection test behind /readyz, so a source is verified
by the same code path that will serve queries — no second database client here.
"""

import asyncio
from dataclasses import dataclass

import httpx

from .registry import Source


@dataclass
class Health:
    ok: bool
    status: str
    detail: str = ""

    def __str__(self) -> str:
        return self.status if not self.detail else f"{self.status} — {self.detail}"


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
