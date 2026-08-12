import time
from dataclasses import dataclass, field

import httpx
import jwt

from ..config import settings
from .schema import CubeQuery

# HS256's key must be at least as long as its output, or the signature is weaker than
# the algorithm claims. pyjwt warns below this on every single token it mints.
MIN_SECRET_BYTES = 32


def mint_token(security_context: dict, ttl_s: int = 900) -> str:
    """Sign the security context Cube requires. Refusing a short secret is louder than pyjwt
    warning per request; the same value must reach the container as CUBEJS_API_SECRET.
    """
    if len(settings.cube_api_secret.encode()) < MIN_SECRET_BYTES:
        raise ValueError(
            f"CUBE_API_SECRET is {len(settings.cube_api_secret.encode())} bytes; HS256 "
            f"needs at least {MIN_SECRET_BYTES}. Generate one with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(36))'` and set it "
            "as both CUBE_API_SECRET and CUBEJS_API_SECRET in .env, then "
            "`make down && make up` so Cube verifies with the same key."
        )
    payload = {**security_context, "exp": int(time.time()) + ttl_s}
    return jwt.encode(payload, settings.cube_api_secret, algorithm="HS256")


@dataclass
class CompileResult:
    ok: bool
    sql: str | None = None
    params: list | None = None
    error: str | None = None


@dataclass
class LoadResult:
    rows: list[dict]
    duration_ms: int
    resolved_ranges: dict[str, list[str]] = field(default_factory=dict)
    """The dateRange Cube resolved, per time dimension. Relative ranges resolve at execution
    time, so this is the only thing that says whether a refresh moved a tile's window."""


class CubeError(RuntimeError):
    pass


class CubeClient:
    def __init__(self, token: str, root: str | None = None, timeout: float | None = None):
        self._root = (root or settings.api_root).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._root,
            headers={"Authorization": token},
            timeout=timeout or settings.query_timeout_s,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def meta(self) -> dict:
        r = await self._client.get("/v1/meta")
        if r.status_code != 200:
            raise CubeError(f"/v1/meta failed [{r.status_code}]: {r.text[:400]}")
        return r.json()

    async def compile(self, query: CubeQuery) -> CompileResult:
        """Tier 3. Cube generates SQL without executing it."""
        r = await self._client.post("/v1/sql", json={"query": query.to_payload()})
        if r.status_code != 200:
            return CompileResult(ok=False, error=_error_text(r))
        body = r.json().get("sql") or {}
        raw = body.get("sql") if isinstance(body, dict) else body
        sql, params = (raw[0], raw[1]) if isinstance(raw, list) and len(raw) > 1 else (raw, None)
        return CompileResult(ok=True, sql=sql, params=params)

    async def load(self, query: CubeQuery) -> LoadResult:
        started = time.perf_counter()
        r = await self._client.post("/v1/load", json={"query": query.to_payload()})
        if r.status_code != 200:
            raise CubeError(_error_text(r))
        body = r.json()

        # Cube answers 200 with an `error` key for some failures rather than a status code, and
        # such a body carries no `data` — so reading rows off it yields [] and a failed query
        # looks like one that matched nothing. `publish._probe`, `read` and `build.build_menu`
        # each learned this and check it by hand; this is the same check, where the rows are.
        if isinstance(body, dict) and body.get("error"):
            raise CubeError(_error_text(r))

        # The window Cube resolved, read off the query it echoes back. Defensively: a tile
        # that cannot label its window is cosmetic, and raising here would fail the query.
        ranges: dict[str, list[str]] = {}
        for td in (body.get("query") or {}).get("timeDimensions") or []:
            member = td.get("dimension")
            window = td.get("dateRange")
            if member and isinstance(window, list) and len(window) == 2:
                ranges[str(member)] = [str(window[0]), str(window[1])]

        return LoadResult(
            rows=body.get("data", []),
            duration_ms=int((time.perf_counter() - started) * 1000),
            resolved_ranges=ranges,
        )


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body)[:600]
    except Exception:
        return f"[{r.status_code}] {r.text[:600]}"
