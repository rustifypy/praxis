import time
from dataclasses import dataclass

import httpx

from ..core.deployment import MAIN, Deployment, current

TIMEOUT_S = 20.0
FILES_TTL_S = 5.0


class StoreError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def missing(self) -> bool:
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
            response = await self._client.request(method, path, headers=headers, **kwargs)
            # Parsed once for both outcomes: a refusal carries `detail`, an answer is the body.
            parsed = response.json()
        except httpx.RequestError as exc:
            raise StoreError(f"{self._root} did not answer: {exc}") from exc
        except ValueError:
            parsed = None
        if response.status_code >= 400:
            detail = (
                str(parsed.get("detail") or "") if isinstance(parsed, dict) else response.text[:300]
            )
            raise StoreError(detail or f"[{response.status_code}]", status=response.status_code)
        return parsed if isinstance(parsed, dict) else {"text": response.text}

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
        # Past the TTL the sha is revalidated rather than the model refetched — one small
        # request instead of the whole thing.
        cached = self._cache.get(ref)
        now = time.monotonic()
        if cached and not refresh:
            checked_at, sha, files = cached
            if now - checked_at < FILES_TTL_S:
                return files
            if await self.version(ref) == sha:
                self._cache[ref] = (now, sha, files)
                return files
        body = await self._send("GET", "/files", params={"ref": ref})
        self._cache[ref] = (now, body["sha"], body["files"])
        return body["files"]

    async def file(self, path: str, ref: str = MAIN) -> str:
        return (await self._send("GET", f"/files/{path}", params={"ref": ref}))["text"]

    async def diff(self, ref: str, base: str = MAIN) -> dict:
        return await self._send("GET", "/diff", params={"ref": ref, "base": base})

    async def log(self, ref: str = MAIN, limit: int = 50) -> list[Commit]:
        body = await self._send("GET", "/log", params={"ref": ref, "limit": limit})
        return [Commit(**commit) for commit in body["commits"]]

    # --- writing ------------------------------------------------------------

    async def replace(self, ref: str, files: dict[str, str], message: str) -> str:
        # The whole model in one commit, which is what makes a rejected rebuild abandonable.
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
    deployment = await current()
    if deployment is None or not deployment.store_url:
        raise StoreError("no model store is configured")
    return for_deployment(deployment, timeout=timeout)


async def model_files(ref: str = MAIN) -> dict[str, str]:
    # Two absences mean "no model here" and answer empty: no store configured, and a ref the
    # store does not have. A configured store that does not answer still raises — an outage
    # reported as an empty model would silently drop the grounding an agent is bound by.
    # Asked of the row rather than caught off `open_store`, so the one handler below can tell a
    # ref that is not there from a store that is not answering — both arrive as a StoreError
    # with no status, and only the second must raise.
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
