# Two questions, not one probe — see docs/adr/0001-reachability-is-two-questions.md before
# merging them. None of these catches a wrong warehouse password; the `read` stage is the first
# thing that connects to the database.

from dataclasses import dataclass, field

import httpx

from .core.deployment import CONTRACT, CUBE_PREFIX, Deployment

# A pasted address with someone watching, versus the recurring probe a page build waits on.
CHECK_TIMEOUT_S = 10.0
LIVE_TIMEOUT_S = 5.0


@dataclass
class Reached:
    # `status` is a word for the chip, `detail` a sentence for the panel beside it, `warnings`
    # what is worth saying but not worth refusing over.
    ok: bool
    status: str = ""
    detail: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "warnings": self.warnings,
        }


def _failed(
    exc: Exception, where: str, timeout: float, warnings: list[str] | None = None
) -> Reached:
    said = warnings or []
    if isinstance(exc, httpx.TimeoutException):
        return Reached(False, "timeout", f"no response from {where} in {timeout:.0f}s", said)
    if isinstance(exc, httpx.ConnectError):
        return Reached(False, "unreachable", f"nothing listening on {where}", said)
    # A booting container accepts the connection and then resets it: ReadError, not ConnectError.
    return Reached(False, "unreachable", f"{type(exc).__name__} reaching {where}", said)


def _rejected(who: str, warnings: list[str] | None = None) -> Reached:
    # One branch for all four callers: this used to be reported as a warehouse outage, blaming
    # the database for a file rendered against another connection.
    return Reached(
        False,
        "rejected",
        f"the {who} rejected the portal's token — this deployment was run from a file "
        "rendered for a different connection",
        warnings or [],
    )


def _mismatch(what: str, theirs: object, symptom: str) -> str:
    # Absent means older than the handshake, which says the same thing as a wrong number.
    return (
        f"this {what} speaks contract {theirs or 'unknown'} and the portal expects {CONTRACT}. "
        f"It may work; if {symptom}, re-run it from a freshly downloaded file."
    )


def _body(response: httpx.Response) -> dict:
    # A JSON object or nothing: raising here would report a live deployment as broken.
    try:
        body = response.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


async def _identified(
    client: httpx.AsyncClient, root: str, what: str, timeout: float
) -> tuple[Reached, dict]:
    # Whether what answered is really this service — something else on the port would read as
    # healthy. The body comes back so the contract needs no second call.
    try:
        answered = await client.get(f"{root}/health")
        if answered.status_code != 200:
            where = f"{root}/health answered {answered.status_code}"
            return Reached(False, "unreachable", where), {}
        body = _body(answered)
        if not body.get("ok"):
            return Reached(False, "wrong service", f"{root} answered, but not as the {what}"), body
        return Reached(True, "ok"), body
    except httpx.RequestError as exc:
        return _failed(exc, root, timeout), {}


async def semantic(url: str, token: str) -> Reached:
    # The store, then Cube through it. Both, because Cube boots slower and is not published, so
    # "store up, Cube not answering" would otherwise pass onboarding and fail every question.
    root = url.rstrip("/")
    warnings: list[str] = []
    # Cube's own path, forwarded verbatim — the same door a query goes through, so this proves
    # the write token as well as the container. Named before the try so the handler can say
    # which leg failed.
    cube = f"{root}{CUBE_PREFIX}/readyz"
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
            found, body = await _identified(client, root, "model store", CHECK_TIMEOUT_S)
            if not found.ok:
                return found
            if body.get("contract") != CONTRACT:
                warnings.append(
                    _mismatch("deployment", body.get("contract"), "something fails oddly")
                )
            answered = await client.get(cube, headers={"Authorization": f"Bearer {token}"})

        if answered.status_code in (401, 403):
            return _rejected("store", warnings)
        if answered.status_code != 200:
            detail = "Cube is not ready behind the store"
            said = _body(answered)
            for key in ("error", "message", "detail", "health"):
                if said.get(key):
                    detail = f"{detail}: {str(said[key])[:200]}"
                    break
            return Reached(False, "not ready", detail, warnings)
        return Reached(True, "ok", "", warnings)
    except httpx.RequestError as exc:
        return _failed(exc, cube, CHECK_TIMEOUT_S, warnings)


async def sandbox(url: str, token: str) -> Reached:
    # `/health` is open by design and cannot prove the token, and a wrong token passes
    # onboarding and dies on the first script.
    root = url.rstrip("/")
    warnings: list[str] = []
    # Any authorised route proves the token; this is the cheapest, and answers for a chat that
    # does not exist. Named before the try so the handler can say which leg failed.
    guarded = f"{root}/profiles/reachability-probe"
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
            found, body = await _identified(client, root, "sandbox", CHECK_TIMEOUT_S)
            if not found.ok:
                return found
            if body.get("contract") != CONTRACT:
                warnings.append(_mismatch("sandbox", body.get("contract"), "scripts fail oddly"))
            answered = await client.get(guarded, headers={"Authorization": f"Bearer {token}"})

        if answered.status_code in (401, 403):
            return _rejected("sandbox", warnings)
        if answered.status_code != 200:
            detail = f"the sandbox answered {answered.status_code}"
            return Reached(False, "not ready", detail, warnings)
        return Reached(True, "ok", "", warnings)
    except httpx.RequestError as exc:
        return _failed(exc, guarded, CHECK_TIMEOUT_S, warnings)


async def querying(deployment: Deployment) -> Reached:
    # One leg: `/cube/readyz` through the store already proves the store is up, that it took
    # the token, and that Cube is behind it.
    if not deployment.store_url:
        return Reached(False, "unconfigured", "no semantic deployment has been set up")
    where = f"{deployment.store_url.rstrip('/')}{CUBE_PREFIX}"
    try:
        async with httpx.AsyncClient(timeout=LIVE_TIMEOUT_S) as client:
            answered = await client.get(
                f"{where}/readyz",
                headers={"Authorization": f"Bearer {deployment.store_token}"},
            )
        if answered.status_code in (401, 403):
            return _rejected("store")
        body = _body(answered)
        status = str(body.get("health") or answered.status_code)
        if answered.status_code == 200:
            return Reached(True, status)
        # Only here is the warehouse a fair thing to blame: the store took the token and Cube
        # answered, so what is left is what Cube itself is unhappy about.
        detail = next(
            (str(body[key])[:300] for key in ("error", "message", "detail") if body.get(key)),
            "Cube reports the data source connection is down",
        )
        return Reached(False, status, detail)
    except httpx.RequestError as exc:
        return _failed(exc, where, LIVE_TIMEOUT_S)


async def alive(deployment: Deployment) -> Reached:
    # Liveness and identity but not the token: answered on every page load, and a stale token
    # is the wizard's to find. Something else on the port is not.
    if not deployment.sandbox_url:
        return Reached(False, "unconfigured", "no sandbox is configured for this deployment")
    root = deployment.sandbox_url.rstrip("/")
    async with httpx.AsyncClient(timeout=LIVE_TIMEOUT_S) as client:
        found, _ = await _identified(client, root, "sandbox", LIVE_TIMEOUT_S)
    return found


async def test(deployment: Deployment, service: str, url: str) -> Reached:
    # The address is a parameter so the wizard can test the box before storing it: a URL that
    # fails need not be undone.
    if not url.strip():
        return Reached(False, "unconfigured", "no address")
    if service == "semantic":
        return await semantic(url, deployment.store_token)
    if service == "sandbox":
        return await sandbox(url, deployment.sandbox_token)
    raise ValueError(f"no such service {service!r}")
