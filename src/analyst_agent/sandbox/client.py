"""The sandbox, over HTTP. Tier 1, and the sibling of `cube/` and `modelstore/`.

Three services, three clients, one shape: the address is on the deployment row, the token is on
it too, and `open_sandbox` reads both fresh so an edited address leaves no stale copy anywhere.

What this replaced was `docker run` and `docker exec` from inside this process. The portal now
runs no containers at all, which is why nothing here knows what docker is.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from ..core.deployment import Deployment, current

TIMEOUT_S = 30.0
RUN_TIMEOUT_S = 180.0
"""Longer than the service's own `EXEC_TIMEOUT_S` so the service is what enforces the limit and
reports it, rather than the client giving up first and leaving a run nobody is reading."""


class SandboxError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def unconfigured(self) -> bool:
        """No sandbox on the row, as against one that is not answering. Code execution is
        optional — a deployment without it still answers questions."""
        return self.status is None and "no sandbox" in str(self)


@dataclass
class Run:
    """What a script did. Mirrors `core.proc.Result` where it overlaps, so `code`/`out`/`err`
    read the same as they did when this was a subprocess."""

    code: int = 0
    out: str = ""
    err: str = ""
    out_log: str = ""
    err_log: str = ""
    script: str = ""
    wrote: list[str] = field(default_factory=list)
    expired: bool = False
    """Killed at the service's timeout rather than exiting on its own."""

    @property
    def ok(self) -> bool:
        return self.code == 0


class SandboxClient:
    def __init__(self, url: str, token: str, *, timeout: float | None = None):
        self._root = url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(base_url=self._root, timeout=timeout or TIMEOUT_S)

    @property
    def root(self) -> str:
        return self._root

    async def __aenter__(self) -> "SandboxClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _send(self, method: str, path: str, **kwargs) -> dict:
        try:
            r = await self._client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.RequestError as exc:
            raise SandboxError(f"{self._root} did not answer: {exc}") from exc
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("detail") or "")
            except ValueError:
                detail = r.text[:300]
            raise SandboxError(detail or f"[{r.status_code}]", status=r.status_code)
        try:
            return r.json()
        except ValueError:
            return {}

    # --- running ------------------------------------------------------------

    async def run(
        self,
        chat_id: str,
        script: str,
        *,
        on_output: Callable[[str], None] | None = None,
    ) -> Run:
        """Run a script and stream what it prints.

        The service sends JSON lines — output as it comes, then exactly one result — so the
        `on_output` seam is the same one `core.proc.run` offered when this was a subprocess.
        Nothing above here had to change shape.
        """
        body = {"chat_id": chat_id, "script": script}
        result: dict = {}
        try:
            async with self._client.stream(
                "POST", "/run", json=body, headers=self._headers(), timeout=RUN_TIMEOUT_S
            ) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    raise SandboxError(
                        raw.decode(errors="replace")[:300], status=response.status_code
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = _parse(line)
                    if (chunk := event.get("output")) and on_output:
                        on_output(chunk)
                    result = event.get("result", result)
        except httpx.RequestError as exc:
            raise SandboxError(f"{self._root} did not answer: {exc}") from exc
        if not result:
            raise SandboxError("the sandbox returned no result")
        return Run(**result)

    async def kill(self, chat_id: str) -> bool:
        """Stop the run. Racing it rather than cancelling the request: the stop flag is only
        tested between chunks, and one long `await` is not interrupted by a chunk."""
        return bool(
            (await self._send("POST", "/kill", json={"chat_id": chat_id, "path": ""})).get("killed")
        )

    # --- files --------------------------------------------------------------

    async def listing(self, chat_id: str) -> tuple[list[str], list[str]]:
        """`(everything, uploads)`. Two lists rather than one plus a filter, because `uploads/`
        being the record of what arrived from a browser is the service's rule, not ours."""
        body = await self._send("GET", f"/files/{chat_id}")
        return body.get("files", []), body.get("uploads", [])

    async def read(self, chat_id: str, path: str) -> tuple[bytes, str]:
        """`(bytes, filename)`. Streamed straight back out by the download route."""
        try:
            r = await self._client.get(f"/files/{chat_id}/{path}", headers=self._headers())
        except httpx.RequestError as exc:
            raise SandboxError(f"{self._root} did not answer: {exc}") from exc
        if r.status_code >= 400:
            raise SandboxError(f"cannot read {path!r}", status=r.status_code)
        return r.content, path.rsplit("/", 1)[-1]

    async def upload(self, chat_id: str, filename: str, data: bytes) -> str:
        body = await self._send("POST", f"/uploads/{chat_id}", files={"file": (filename, data)})
        return body["path"]

    async def delete(self, chat_id: str, path: str) -> None:
        await self._send("DELETE", f"/files/{chat_id}/{path}")

    async def forget(self, chat_id: str) -> None:
        """Deleting a chat takes its workspace with it — the directory *is* the record."""
        await self._send("DELETE", f"/workspace/{chat_id}")

    async def profile(self, chat_id: str, path: str) -> dict | None:
        body = await self._send("POST", "/profile", json={"chat_id": chat_id, "path": path})
        return body.get("profile")


def _parse(line: str) -> dict:
    import json

    try:
        parsed = json.loads(line)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def for_deployment(deployment: Deployment, *, timeout: float | None = None) -> SandboxClient:
    return SandboxClient(deployment.sandbox_url, deployment.sandbox_token, timeout=timeout)


async def open_sandbox(*, timeout: float | None = None) -> SandboxClient:
    deployment = await current()
    if deployment is None or not deployment.sandbox_url:
        raise SandboxError("no sandbox is configured for this deployment")
    return for_deployment(deployment, timeout=timeout)
