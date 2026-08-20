"""The model store, for as long as you hold it. Whoever constructs it closes it.

Every call to the store goes through here, so a caller cannot forget the write token. The
model is not on this filesystem any more, so the two things the portal used to get from disk
for free — reading main's blobs, and listing the model — are network calls, and `files` is
cached against `version` because grounding is read on every chat turn.
"""

import time
from dataclasses import dataclass

import httpx

from ..core.deployment import MAIN, Deployment, current

TIMEOUT_S = 20.0
FILES_TTL_S = 5.0
"""How long a cached file set is reused without re-checking `version`. Bounded staleness
rather than invalidation: nothing watches the store, and the alternative is a round trip per
turn for a model that changes when someone presses a button."""


class StoreError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def missing(self) -> bool:
        """A ref or a path that is not there, as against the store being unreachable."""
        return self.status == 404


@dataclass
class Commit:
    sha: str
    author: str
    at: float
    subject: str


class ModelStore:
    def __init__(self, url: str, token: str, *, timeout: float | None = None):
        self._root = url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(base_url=self._root, timeout=timeout or TIMEOUT_S)
        # ref -> (checked_at, sha, files)
        self._cache: dict[str, tuple[float, str, dict[str, str]]] = {}

    @property
    def root(self) -> str:
        return self._root

    async def __aenter__(self) -> "ModelStore":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            r = await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise StoreError(f"{self._root} did not answer: {exc}") from exc
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("detail") or "")
            except ValueError:
                detail = r.text[:300]
            raise StoreError(detail or f"[{r.status_code}]", status=r.status_code)
        try:
            return r.json()
        except ValueError:
            return {"text": r.text}

    # --- refs ---------------------------------------------------------------

    async def refs(self) -> list[dict]:
        return (await self._send("GET", "/refs"))["refs"]

    async def create_ref(self, name: str, from_ref: str = MAIN) -> str:
        body = await self._send("POST", "/refs", json={"name": name, "from_ref": from_ref})
        return body["sha"]

    async def delete_ref(self, name: str) -> None:
        await self._send("DELETE", f"/refs/{name}")
        self._cache.pop(name, None)

    async def version(self, ref: str = MAIN) -> str:
        return (await self._send("GET", "/version", params={"ref": ref}))["sha"]

    # --- reading ------------------------------------------------------------

    async def files(self, ref: str = MAIN, *, refresh: bool = False) -> dict[str, str]:
        """Every model file at `ref`. Cached for `FILES_TTL_S`, then revalidated against the
        sha — which is one small request rather than the whole model."""
        hit = self._cache.get(ref)
        now = time.monotonic()
        if hit and not refresh and now - hit[0] < FILES_TTL_S:
            return hit[2]
        if hit and not refresh and await self.version(ref) == hit[1]:
            self._cache[ref] = (now, hit[1], hit[2])
            return hit[2]
        body = await self._send("GET", "/files", params={"ref": ref})
        self._cache[ref] = (now, body["sha"], body["files"])
        return body["files"]

    async def file(self, path: str, ref: str = MAIN) -> str:
        return (await self._send("GET", f"/files/{path}", params={"ref": ref}))["text"]

    async def diff(self, ref: str, base: str = MAIN) -> dict:
        return await self._send("GET", "/diff", params={"ref": ref, "base": base})

    async def log(self, ref: str = MAIN, limit: int = 50) -> list[Commit]:
        body = await self._send("GET", "/log", params={"ref": ref, "limit": limit})
        return [Commit(**c) for c in body["commits"]]

    # --- writing ------------------------------------------------------------

    async def replace(self, ref: str, files: dict[str, str], message: str) -> str:
        """The whole model at `ref`, in one commit. What a rebuild does — and because it is a
        commit on a ref rather than an unlink-and-write, a rejected model is abandonable."""
        body = await self._send(
            "PUT", "/files", params={"ref": ref}, json={"files": files, "message": message}
        )
        self._cache.pop(ref, None)
        return body["sha"]

    async def edit(
        self,
        ref: str,
        write: dict[str, str] | None = None,
        delete: list[str] | None = None,
        message: str = "edit model",
    ) -> str:
        body = await self._send(
            "PATCH",
            "/files",
            params={"ref": ref},
            json={"write": write or {}, "delete": delete or [], "message": message},
        )
        self._cache.pop(ref, None)
        return body["sha"]

    async def merge(self, from_ref: str, into: str = MAIN, message: str = "") -> str:
        body = await self._send(
            "POST", "/merge", json={"from_ref": from_ref, "into": into, "message": message}
        )
        self._cache.pop(into, None)
        return body["sha"]


def for_deployment(deployment: Deployment, *, timeout: float | None = None) -> ModelStore:
    return ModelStore(deployment.store_url, deployment.store_token, timeout=timeout)


async def open_store(*, timeout: float | None = None) -> ModelStore:
    """A store client on the configured deployment, read fresh from the row — the same reason
    `open_client` does: an edited address must not leave a stale copy anywhere."""
    deployment = await current()
    if deployment is None or not deployment.store_url:
        raise StoreError("no model store is configured")
    return for_deployment(deployment, timeout=timeout)


async def model_files(ref: str = MAIN) -> dict[str, str]:
    """The model at `ref`, or nothing at all.

    Two absences answer empty rather than raising, because both mean "there is no model here":
    a deployment with no store (every deployment until one is configured) and a ref the store
    does not have (a candidate that was cleaned up). A store that *is* configured and does not
    answer still raises — an outage reported as an empty model would erase the pipeline's rail
    and silently drop the grounding an agent is bound by.

    One function because five callers need that rule and it is the kind that drifts.
    """
    try:
        opened = await open_store()
    except StoreError:
        return {}
    async with opened as store:
        try:
            return await store.files(ref)
        except StoreError as exc:
            if not exc.missing:
                raise
            return {}
