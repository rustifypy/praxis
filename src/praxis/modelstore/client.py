"""The model store's HTTP API and the Cube behind it, as the rest of this package wants it.

One service at one address. The store owns the model's files and their history *and* proxies Cube
at `/refs/{ref}/…`, minting Cube's JWT from the ref in the path — so there is one session, one
token and one error type here. Two clients to the same host would be two connection pools and two
things to catch for one deployment.

Two halves with two different manners, and the difference is load-bearing:

- The **model** half raises. A 4xx is the store refusing, and every caller of `files` or `edit`
  wants that as an exception.
- The **query** half returns. Cube rejecting a query is the gate's raw material — it reaches the
  agent as text it can act on — so `compile` answers `ok=False` and only an unreachable
  deployment raises. `load` is the exception to the exception: `gate.execute` catches it and
  turns it into a refusal, so raising there costs nothing and keeps `LoadResult` free of an
  `ok` nobody would read.

`StoreError.where` says which half failed. It exists for `editing.workspace`, which reports "the
store refused this change" and "Cube did not answer" as different sentences from one `except`.

The store has no bulk read, no bulk write, and no status on a diff; those are fanned out and
reassembled here so nothing above this tier knows.
"""

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from ..core.config import settings
from ..core.deployment import MAIN, Deployment, current
from .catalog import Catalog
from .schema import CubeQuery

TIMEOUT_S = 20.0
FILES_TTL_S = 5.0
CATALOG_TTL_S = 30.0

# Cube's routes under the store's proxy. The ref is a path segment rather than a header: the
# store mints the JWT for whichever ref the route names, so there is nothing for a caller to
# sign or send.
ROUTES = {"sql": "sql", "load": "query", "meta": "meta"}

# Cube-bound, so all of these stay under the store's proxy cap: above it they can never elapse,
# and a slow query arrives as a 502 instead. Same rule for `settings.query_timeout_s`.
ADMIN_TIMEOUT_S = 10.0
POLL_EVERY_S = 2.0
POLL_TIMEOUT_S = 90.0
POLL_REQUEST_TIMEOUT_S = 15.0


class StoreError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, where: str = "store"):
        super().__init__(message)
        self.status = status
        # `store` or `cube`. The address is the same; what is behind it at that path is not.
        self.where = where

    @property
    def missing(self) -> bool:
        return self.status == 404


@dataclass
class Ref:
    name: str
    sha: str
    rev: int
    dirty: bool


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
    # The dateRange Cube resolved, per time dimension. Relative ranges resolve at execution
    # time, so this is the only thing saying whether a refresh moved a tile's window.
    resolved_ranges: dict[str, list[str]] = field(default_factory=dict)


def _ref(body: dict) -> Ref:
    return Ref(
        name=str(body["ref"]),
        sha=str(body.get("sha") or ""),
        rev=int(body.get("rev") or 0),
        dirty=bool(body.get("dirty")),
    )


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or f"[{response.status_code}]"
    if isinstance(body, dict):
        said = str(body.get("error") or body.get("detail") or "")
        return said[:300] or f"[{response.status_code}]"
    return f"[{response.status_code}]"


def _error_of(body: dict) -> str:
    return str(body.get("error") or "") if isinstance(body, dict) else ""


def _error_text(body: dict, text: str, status: int) -> str:
    if error := _error_of(body):
        return error[:600]
    return str(body or text)[:600] if status == 200 else f"[{status}] {text[:600]}"


class ModelStore:
    def __init__(self, url: str, token: str, *, timeout: float | None = None):
        self._root = url.rstrip("/")
        # An unset token is a first run, not a mistake, and normalises to no auth.
        self._token = token or ""
        # The session's own cap covers the model half, whose calls are small and local to the
        # store. Cube-bound calls pass their own — `settings.query_timeout_s` by default — so the
        # two bands are named at the call rather than decided by which object a caller built.
        self._client = httpx.AsyncClient(base_url=self._root, timeout=timeout or TIMEOUT_S)
        # ref -> (checked_at, rev, files)
        self._files: dict[str, tuple[float, int, dict[str, str]]] = {}
        # ref -> (read_at, catalog)
        self._catalogs: dict[str, tuple[float, Catalog]] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def for_deployment(cls, deployment: Deployment, *, timeout: float | None = None):
        return cls(deployment.store_url, deployment.store_token, timeout=timeout)

    @property
    def root(self) -> str:
        return self._root

    def at(self, ref: str = MAIN, *, timeout: float | None = None) -> "Bound":
        """This store, asked about one ref. What `gate`, `Session` and a refresh take."""
        return Bound(self, ref, timeout)

    async def __aenter__(self) -> "ModelStore":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    # --- transport ----------------------------------------------------------

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        """The model half. Raises on 4xx, because a refusal from the store is an error."""
        try:
            response = await self._client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.RequestError as exc:
            raise StoreError(f"{self._root} did not answer: {exc}") from exc
        if response.status_code >= 400:
            raise StoreError(_detail(response), status=response.status_code)
        return response

    async def _ask(
        self, method: str, route: str, ref: str, *, json: dict | None = None, timeout: float | None
    ) -> tuple[int, dict, str]:
        """The query half. Returns the status rather than raising on it — Cube's rejection is
        the gate's material. Only a transport failure raises, so no caller has to know that
        `httpx.RequestError` does not subclass OSError."""
        try:
            response = await self._client.request(
                method,
                f"/refs/{ref}/{route}",
                json=json,
                headers=self._headers(),
                timeout=settings.query_timeout_s if timeout is None else timeout,
            )
            body = response.json()
        except httpx.RequestError as exc:
            raise StoreError(f"{self._root} did not answer: {exc}", where="cube") from exc
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}, response.text

    # --- refs ---------------------------------------------------------------

    async def refs(self) -> list[Ref]:
        return [_ref(body) for body in (await self._send("GET", "/refs")).json()]

    async def create_ref(self, name: str, from_ref: str = MAIN) -> Ref:
        # `from`, not `from_ref` — the store's own field name, and one it would silently
        # default to main on.
        body = {"name": name, "from": from_ref}
        return _ref((await self._send("POST", "/refs", json=body)).json())

    async def delete_ref(self, name: str) -> None:
        await self._send("DELETE", f"/refs/{name}")
        self._forget(name)

    async def rev(self, ref: str = MAIN) -> int:
        return int((await self._send("GET", f"/refs/{ref}")).json()["rev"])

    async def rebase(self, ref: str) -> int:
        # What makes a ref mergeable again once main has moved. 409 on conflict, which the
        # publish path answers by recreating the ref rather than resolving anything.
        body = (await self._send("POST", f"/refs/{ref}/rebase")).json()
        self._forget(ref)
        return int(body["rev"])

    def _forget(self, ref: str) -> None:
        # Both caches, because a ref whose files moved is a ref whose compiled model moved —
        # Cube reads the store's revision on every request, so a stale catalog outlives nothing.
        self._files.pop(ref, None)
        self._catalogs.pop(ref, None)

    # --- reading ------------------------------------------------------------

    async def _listing(self, ref: str) -> tuple[int, list[str]]:
        body = (await self._send("GET", f"/refs/{ref}/tree")).json()
        return int(body["rev"]), [entry["path"] for entry in body["files"]]

    async def files(self, ref: str = MAIN, *, refresh: bool = False) -> dict[str, str]:
        # Past the TTL the rev is revalidated rather than the model refetched — one small
        # request instead of one per file.
        cached = self._files.get(ref)
        now = time.monotonic()
        if cached and not refresh:
            checked_at, at_rev, files = cached
            if now - checked_at < FILES_TTL_S:
                return files
            if await self.rev(ref) == at_rev:
                self._files[ref] = (now, at_rev, files)
                return files
        at_rev, paths = await self._listing(ref)
        bodies = await asyncio.gather(*(self.file(path, ref) for path in paths))
        files = dict(zip(paths, bodies, strict=True))
        self._files[ref] = (now, at_rev, files)
        return files

    async def file(self, path: str, ref: str = MAIN) -> str:
        return (await self._send("GET", f"/refs/{ref}/files/{path}")).text

    async def diff(self, ref: str, base: str = MAIN) -> dict:
        body = (await self._send("GET", f"/refs/{ref}/diff", params={"against": base})).json()
        # The store reports which paths changed but not how. A reviewer reads add and delete
        # differently from an edit, so the status is recovered by comparing the two listings.
        _, here = await self._listing(ref)
        _, there = await self._listing(base)
        mine, theirs = set(here), set(there)
        changed = [
            {
                "status": "A" if path not in theirs else "D" if path not in mine else "M",
                "path": path,
            }
            for path in body.get("files") or []
        ]
        return {"changed": changed, "patch": body.get("patch") or ""}

    # --- writing ------------------------------------------------------------

    async def replace(self, ref: str, files: dict[str, str], message: str) -> int:
        _, existing = await self._listing(ref)
        return await self.edit(ref, files, [p for p in existing if p not in files], message)

    async def edit(
        self,
        ref: str,
        write: dict[str, str] | None = None,
        delete: list[str] | None = None,
        message: str = "edit model",
    ) -> int:
        """A change set as one commit. Returns the ref's revision afterwards.

        The store takes one file per request and commits separately, so this is several calls
        that must not half-land: a set spanning two files cannot compile one at a time.
        """
        try:
            at_rev, changed = await self.rev(ref), False
            # Deletes first. A model missing a file compiles; one carrying a stale file beside
            # its replacement is what does not.
            for path in delete or []:
                try:
                    await self._send("DELETE", f"/refs/{ref}/files/{path}")
                    changed = True
                except StoreError as exc:
                    # Already gone is the state being asked for.
                    if not exc.missing:
                        raise
            for path, text in (write or {}).items():
                answered = (
                    await self._send("PUT", f"/refs/{ref}/files/{path}", content=text.encode())
                ).json()
                at_rev = int(answered.get("rev") or at_rev)
                changed = changed or bool(answered.get("changed"))
            if changed:
                # Refused with 409 when nothing is staged, so a set that turned out to be a
                # no-op must not reach it — rewriting a file with its own bytes is not an edit.
                await self._send("POST", f"/refs/{ref}/commit", json={"message": message})
            return at_rev
        finally:
            self._forget(ref)

    async def merge(self, from_ref: str, into: str = MAIN) -> int:
        # Fast-forward into main, and the store offers no other target: `into` is here so a
        # caller naming something else is refused rather than quietly landing on main.
        if into != MAIN:
            raise StoreError(f"the store fast-forwards into {MAIN} only, not {into!r}")
        body = (await self._send("POST", f"/refs/{from_ref}/merge")).json()
        self._forget(into)
        return int(body["rev"])

    # --- Cube, through the store --------------------------------------------

    async def catalog(
        self, ref: str = MAIN, *, refresh: bool = False, timeout: float | None = None
    ) -> Catalog:
        async with self._lock:
            read_at, cached = self._catalogs.get(ref, (0.0, None))
            if refresh or cached is None or time.monotonic() - read_at >= CATALOG_TTL_S:
                cached = Catalog.from_meta(await self._meta(ref, timeout=timeout))
                self._catalogs[ref] = (time.monotonic(), cached)
            return cached

    async def compile(
        self, query: CubeQuery, ref: str = MAIN, *, timeout: float | None = None
    ) -> CompileResult:
        # Tier 3. A query Cube rejects comes back as ok=False — that is the gate's business;
        # only an unreachable container raises.
        status, body, text = await self._ask(
            "POST", ROUTES["sql"], ref, json={"query": query.to_payload()}, timeout=timeout
        )
        if status != 200 or _error_of(body):
            return CompileResult(ok=False, error=_error_text(body, text, status))
        sql = body.get("sql") or {}
        raw = sql.get("sql") if isinstance(sql, dict) else sql
        head, params = (raw[0], raw[1]) if isinstance(raw, list) and len(raw) > 1 else (raw, None)
        return CompileResult(ok=True, sql=head, params=params)

    async def load(
        self, query: CubeQuery, ref: str = MAIN, *, timeout: float | None = None
    ) -> LoadResult:
        started = time.perf_counter()
        status, body, text = await self._ask(
            "POST", ROUTES["load"], ref, json={"query": query.to_payload()}, timeout=timeout
        )
        # Cube reports some failures as 200 with an `error` key and no `data`, so reading rows off
        # such a body turns a failed query into one that matched nothing.
        if status != 200 or _error_of(body):
            raise StoreError(_error_text(body, text, status), status=status, where="cube")

        # The window Cube resolved, read off the query it echoes back. Defensively: a tile
        # that cannot label its window is cosmetic, and raising here would fail the query.
        ranges: dict[str, list[str]] = {}
        for time_dimension in (body.get("query") or {}).get("timeDimensions") or []:
            member = time_dimension.get("dimension")
            window = time_dimension.get("dateRange")
            if member and isinstance(window, list) and len(window) == 2:
                ranges[str(member)] = [str(window[0]), str(window[1])]

        return LoadResult(
            rows=body.get("data", []),
            duration_ms=int((time.perf_counter() - started) * 1000),
            resolved_ranges=ranges,
        )

    async def wait_until_compiled(
        self, ref: str = MAIN, *, timeout: float = POLL_TIMEOUT_S, every: float = POLL_EVERY_S
    ) -> Catalog:
        # A booting container refuses connections and answers slowly, so neither is terminal;
        # a compile error is.
        deadline = asyncio.get_running_loop().time() + timeout
        last = "no response"
        while asyncio.get_running_loop().time() < deadline:
            try:
                meta = await self._meta(ref, timeout=POLL_REQUEST_TIMEOUT_S)
            except StoreError as exc:
                # Cube names the offending file in a compile error, and waiting out the deadline
                # would throw that away. Anything else is a container still coming up.
                if "Compile errors" in str(exc) or "Error: " in str(exc):
                    raise
                last = str(exc)[:300]
            else:
                if "cubes" in meta:
                    async with self._lock:
                        compiled = Catalog.from_meta(meta)
                        self._catalogs[ref] = (time.monotonic(), compiled)
                    return compiled
                last = str(meta)[:300]
            await asyncio.sleep(every)
        raise StoreError(f"no compiled model within {timeout:.0f}s — {last}", where="cube")

    async def _meta(self, ref: str, *, timeout: float | None = None) -> dict:
        status, body, text = await self._ask("GET", ROUTES["meta"], ref, timeout=timeout)
        if status != 200 or _error_of(body):
            raise StoreError(_error_text(body, text, status), status=status, where="cube")
        return body


@dataclass(frozen=True)
class Bound:
    """A store and one ref. Everything that queries is pinned to a ref for its whole life —
    a chat, a dashboard, a candidate being compiled — so they take this rather than passing
    the same string to every call and eventually passing two different ones.

    The model half is deliberately absent: refs are created, diffed and merged *across* refs,
    and a view pinned to one of them would have to break its own pinning to do it.
    """

    store: ModelStore
    ref: str = MAIN
    timeout: float | None = None

    @property
    def root(self) -> str:
        return self.store.root

    async def catalog(self, *, refresh: bool = False) -> Catalog:
        return await self.store.catalog(self.ref, refresh=refresh, timeout=self.timeout)

    async def compile(self, query: CubeQuery) -> CompileResult:
        return await self.store.compile(query, self.ref, timeout=self.timeout)

    async def load(self, query: CubeQuery) -> LoadResult:
        return await self.store.load(query, self.ref, timeout=self.timeout)

    async def wait_until_compiled(
        self, *, timeout: float = POLL_TIMEOUT_S, every: float = POLL_EVERY_S
    ) -> Catalog:
        return await self.store.wait_until_compiled(self.ref, timeout=timeout, every=every)


def for_deployment(deployment: Deployment, *, timeout: float | None = None) -> ModelStore:
    return ModelStore.for_deployment(deployment, timeout=timeout)


async def open_store(*, timeout: float | None = None) -> ModelStore:
    deployment = await current()
    if deployment is None or not deployment.store_url:
        raise StoreError("no model store is configured")
    return for_deployment(deployment, timeout=timeout)


async def model_files(ref: str = MAIN) -> dict[str, str]:
    # Two absences mean "no model here" and answer empty: no store configured, and a ref the
    # store does not have. A configured store that does not answer still raises — an outage
    # reported as an empty model would silently drop the model an agent is bound by.
    deployment = await current()
    if deployment is None or not deployment.store_url:
        return {}
    try:
        async with for_deployment(deployment) as store:
            return await store.files(ref)
    except StoreError as exc:
        if not exc.missing:
            raise
        return {}


async def served_catalog(*, ref: str = MAIN, timeout: float = ADMIN_TIMEOUT_S) -> Catalog:
    async with await open_store() as store:
        return await store.at(ref, timeout=timeout).catalog()
