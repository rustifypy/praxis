"""One session against Cube — which is reached **through the model store**, never directly.

Cube's port is not published, so the store at `/cube/...` is the only door and this client holds
the store's write token rather than a Cube JWT. The ref goes in `X-Model-Ref` and the store signs
it: Cube's secret never reaches this process, which is one credential fewer here and the reason
`CUBEJS_DEV_MODE` disabling JWT verification is not something the host can exploit.

Every call goes through here, so a caller cannot forget to authenticate. The paths are Cube's own
and are forwarded verbatim, so `/readyz` and `/v1/load` differ only in what they are.
"""

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from ..core.config import settings
from ..core.deployment import MAIN, Deployment, current
from .catalog import Catalog
from .schema import CubeQuery

CUBE_PREFIX = "/cube"
"""Where the store proxies Cube. One constant, because the store's route and this base URL are
the two halves of one path and a mismatch is a 404 with nothing to say."""
REF_HEADER = "X-Model-Ref"
"""Which model to compile. The store signs it into the token it mints — this process never
holds a credential that could name a ref by itself."""

# How stale a served catalog may be. Bounded staleness rather than invalidation, because
# nothing watches Cube and the cache that had to be invalidated by hand was keyed on a global.
CATALOG_TTL_S = 30.0
ADMIN_TIMEOUT_S = 10.0
POLL_EVERY_S = 2.0
POLL_TIMEOUT_S = 90.0
POLL_REQUEST_TIMEOUT_S = 15.0


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
    def __init__(self, message: str, *, status: int | None = None, cause: Exception | None = None):
        super().__init__(message)
        self.status = status
        self.cause = cause

    @property
    def unreachable(self) -> bool:
        """Nothing answered at all, as against Cube answering badly. `health.serving` reports a
        stopped container differently from one serving no model."""
        return self.cause is not None


class CubeClient:
    """Cube, for as long as you hold it. Whoever constructs it closes it."""

    def __init__(
        self,
        root: str,
        token: str | None,
        *,
        ref: str = MAIN,
        timeout: float | None = None,
    ):
        # An unset token is a first run, not a mistake, and normalises to no auth.
        self._token = token or ""
        self._root = root.rstrip("/")
        self._ref = ref
        self._client = httpx.AsyncClient(
            base_url=self._root, timeout=timeout or settings.query_timeout_s
        )
        self._catalog: Catalog | None = None
        self._catalog_at = 0.0
        self._lock = asyncio.Lock()

    @classmethod
    def for_deployment(
        cls,
        deployment: Deployment,
        *,
        ref: str = MAIN,
        timeout: float | None = None,
    ) -> "CubeClient":
        """`ref` selects the model Cube compiles for this session, so a client is per-ref rather
        than shared. It reaches Cube through the store, which is the only thing that can sign it
        into a Cube token."""
        return cls(
            f"{deployment.store_url.rstrip('/')}{CUBE_PREFIX}{settings.cube_base_path}",
            deployment.store_token,
            ref=ref,
            timeout=timeout,
        )

    @property
    def ref(self) -> str:
        return self._ref

    @property
    def root(self) -> str:
        return self._root

    async def __aenter__(self) -> "CubeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def catalog(self, *, refresh: bool = False) -> Catalog:
        """The served model. Cached for `CATALOG_TTL_S`; concurrent callers wait on one read."""
        async with self._lock:
            age = time.monotonic() - self._catalog_at
            if refresh or self._catalog is None or age >= CATALOG_TTL_S:
                self._catalog = Catalog.from_meta(await self._meta())
                self._catalog_at = time.monotonic()
            return self._catalog

    async def compile(self, query: CubeQuery) -> CompileResult:
        """Tier 3. Cube generates SQL without executing it. A query Cube rejects comes back as
        `ok=False` — that is the gate's business; only an unreachable container raises."""
        status, body, text = await self._send("POST", "/v1/sql", json={"query": query.to_payload()})
        if status != 200 or _error_of(body):
            return CompileResult(ok=False, error=_error_text(body, text, status))
        sql = body.get("sql") or {}
        raw = sql.get("sql") if isinstance(sql, dict) else sql
        head, params = (raw[0], raw[1]) if isinstance(raw, list) and len(raw) > 1 else (raw, None)
        return CompileResult(ok=True, sql=head, params=params)

    async def load(self, query: CubeQuery) -> LoadResult:
        """Tier 4."""
        started = time.perf_counter()
        status, body, text = await self._send(
            "POST", "/v1/load", json={"query": query.to_payload()}
        )
        # Cube reports some failures as 200 with an `error` key and no `data`, so reading rows off
        # such a body turns a failed query into one that matched nothing.
        if status != 200 or _error_of(body):
            raise CubeError(_error_text(body, text, status), status=status)

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

    async def wait_until_compiled(
        self, *, timeout: float = POLL_TIMEOUT_S, every: float = POLL_EVERY_S
    ) -> Catalog:
        """Poll until Cube serves a model, then return it. A booting container refuses
        connections and answers slowly, so neither is terminal — a compile error is.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        last = "no response"
        while asyncio.get_running_loop().time() < deadline:
            try:
                meta = await self._meta(timeout=POLL_REQUEST_TIMEOUT_S)
            except CubeError as exc:
                # Cube names the offending file in a compile error, and waiting out the deadline
                # would throw that away. Anything else is a container still coming up.
                if "Compile errors" in str(exc) or "Error: " in str(exc):
                    raise
                last = str(exc)[:300]
            else:
                if "cubes" in meta:
                    async with self._lock:
                        self._catalog = Catalog.from_meta(meta)
                        self._catalog_at = time.monotonic()
                    return self._catalog
                last = str(meta)[:300]
            await asyncio.sleep(every)
        raise CubeError(f"no compiled model within {timeout:.0f}s — {last}")

    async def _meta(self, *, timeout: float | None = None) -> dict:
        status, body, text = await self._send("GET", "/v1/meta", timeout=timeout)
        if status != 200 or _error_of(body):
            raise CubeError(_error_text(body, text, status), status=status)
        return body

    def _headers(self) -> dict[str, str]:
        """The store's token and the ref. No expiry to manage: the Cube JWT is minted per request
        on the other side, which is what removed the re-mint window this used to carry."""
        headers = {REF_HEADER: self._ref}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _send(
        self, method: str, path: str, *, json: dict | None = None, timeout: float | None = None
    ) -> tuple[int, dict, str]:
        """Status, parsed body, raw text. Transport failures become `CubeError` so no caller has
        to know that `httpx.RequestError` does not subclass `OSError`."""
        try:
            r = await self._client.request(
                method,
                path,
                json=json,
                headers=self._headers(),
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.RequestError as exc:
            raise CubeError(f"{self._root} did not answer: {exc}", cause=exc) from exc
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}, r.text


async def open_client(*, ref: str = MAIN, timeout: float | None = None) -> CubeClient:
    """A client on the configured deployment, read fresh from the row. What the callers too
    deep to be handed one use, so an edited address leaves no stale copy anywhere."""
    deployment = await current()
    if deployment is None or not deployment.store_url:
        raise CubeError("no Cube deployment is configured")
    return CubeClient.for_deployment(deployment, ref=ref, timeout=timeout)


async def served_catalog(*, ref: str = MAIN, timeout: float = ADMIN_TIMEOUT_S) -> Catalog:
    """The served model, read once. The admin screens want exactly this and nothing else."""
    async with await open_client(ref=ref, timeout=timeout) as cube:
        return await cube.catalog()


def _error_of(body: dict) -> str:
    return str(body.get("error") or "") if isinstance(body, dict) else ""


def _error_text(body: dict, text: str, status: int) -> str:
    if error := _error_of(body):
        return error[:600]
    return str(body or text)[:600] if status == 200 else f"[{status}] {text[:600]}"
