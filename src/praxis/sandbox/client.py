import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import BinaryIO

import httpx

from ..core.deployment import Deployment, current

TIMEOUT_S = 30.0

# The cap that fires first is the one that reports. These two sit above the service's own, so it
# enforces and reports; a client giving up first abandons a run still holding the workspace, or an
# upload that already landed. Cube-bound caps go the other way — see `cube/client.py`.
# `tests/test_timeouts.py` asserts both directions.
RUN_TIMEOUT_S = 180.0
UPLOAD_TIMEOUT_S = 120.0


class SandboxError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Run:
    code: int = 0
    out: str = ""
    err: str = ""
    out_log: str = ""
    err_log: str = ""
    script: str = ""
    wrote: list[str] = field(default_factory=list)
    # Killed at the service's timeout rather than exiting on its own.
    expired: bool = False

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
            response = await self._client.request(method, path, headers=self._headers(), **kwargs)
            # Parsed once for both outcomes: a refusal carries `detail`, an answer is the body.
            parsed = response.json()
        except httpx.RequestError as exc:
            raise SandboxError(f"{self._root} did not answer: {exc}") from exc
        except ValueError:
            parsed = None
        if response.status_code >= 400:
            detail = (
                str(parsed.get("detail") or "") if isinstance(parsed, dict) else response.text[:300]
            )
            raise SandboxError(detail or f"[{response.status_code}]", status=response.status_code)
        return parsed if isinstance(parsed, dict) else {}

    # --- running ------------------------------------------------------------

    async def run(
        self,
        chat_id: str,
        script: str,
        *,
        on_output: Callable[[str], None] | None = None,
    ) -> Run:
        # The service sends JSON lines: output as it comes, then exactly one result.
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
            if not result:
                raise SandboxError("the sandbox returned no result")
            return Run(**result)
        except httpx.RequestError as exc:
            raise SandboxError(f"{self._root} did not answer: {exc}") from exc

    async def kill(self, chat_id: str) -> bool:
        return bool(
            (await self._send("POST", "/kill", json={"chat_id": chat_id, "path": ""})).get("killed")
        )

    # --- files --------------------------------------------------------------

    async def listing(self, chat_id: str) -> tuple[list[str], list[str]]:
        # (everything, uploads). Two lists rather than one plus a filter: `uploads/` being the
        # record of what arrived from a browser is the service's rule, not ours.
        body = await self._send("GET", f"/files/{chat_id}")
        return body.get("files", []), body.get("uploads", [])

    async def read(self, chat_id: str, path: str) -> tuple[bytes, str]:
        try:
            response = await self._client.get(f"/files/{chat_id}/{path}", headers=self._headers())
            if response.status_code >= 400:
                raise SandboxError(f"cannot read {path!r}", status=response.status_code)
            return response.content, path.rsplit("/", 1)[-1]
        except httpx.RequestError as exc:
            raise SandboxError(f"{self._root} did not answer: {exc}") from exc

    async def upload(self, chat_id: str, filename: str, data: bytes | BinaryIO) -> dict:
        # The whole body, so a caller wanting the profile does not reach past this for it —
        # which is also what keeps UPLOAD_TIMEOUT_S in one place. An open file is streamed by
        # httpx rather than read whole, which is what lets a Phase hand over a large extract.
        return await self._send(
            "POST",
            f"/uploads/{chat_id}",
            files={"file": (filename, data)},
            timeout=UPLOAD_TIMEOUT_S,
        )

    async def delete(self, chat_id: str, path: str) -> None:
        await self._send("DELETE", f"/files/{chat_id}/{path}")

    async def forget(self, chat_id: str) -> None:
        await self._send("DELETE", f"/workspace/{chat_id}")

    async def profiles(self, chat_id: str) -> list[dict]:
        # Raw dicts: `Profile` lives beside the prompt rendering and imports this module.
        return (await self._send("GET", f"/profiles/{chat_id}")).get("profiles", [])


def _parse(line: str) -> dict:
    try:
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def for_deployment(deployment: Deployment, *, timeout: float | None = None) -> SandboxClient:
    return SandboxClient(deployment.sandbox_url, deployment.sandbox_token, timeout=timeout)


async def open_sandbox(*, timeout: float | None = None) -> SandboxClient:
    deployment = await current()
    if deployment is None or not deployment.sandbox_url:
        raise SandboxError("no sandbox is configured for this deployment")
    return for_deployment(deployment, timeout=timeout)
