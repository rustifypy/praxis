import time
from dataclasses import dataclass

import httpx
import jwt

from ..config import settings
from .schema import CubeQuery


def mint_token(security_context: dict, ttl_s: int = 900) -> str:
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
        return LoadResult(
            rows=body.get("data", []),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        return str(body.get("error") or body)[:600]
    except Exception:
        return f"[{r.status_code}] {r.text[:600]}"
