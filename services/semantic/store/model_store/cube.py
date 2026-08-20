"""Cube, behind this service.

The store is the only door into the deployment: Cube's port is not published, so nothing reaches
it except through here. That buys two things. Cube's JWT never leaves the compose network — it is
minted per request and the secret it is signed with is not a credential anyone outside holds. And
`CUBEJS_DEV_MODE=true` disabling JWT verification stops being a hole reachable from the host.

**The ref is why this belongs here rather than in a generic proxy.** A ref is what selects the
model Cube compiles (`cube.js`'s `contextToAppId` reads it off the security context), and this
service is the only thing that knows what a ref is. So the caller names one in a header and this
signs it; the caller never handles a Cube token at all.

Otherwise it is a **pure pass-through** — the path is forwarded verbatim, so nothing here knows
that `/cubejs-api/v1/load` is a query and `/readyz` is a health check. One less thing to get
wrong when Cube's routing changes.
"""

import os

import httpx
import jwt

CUBE_URL = os.environ.get("CUBE_URL", "http://cube:4000").rstrip("/")
API_SECRET = os.environ.get("CUBEJS_API_SECRET", "")
REF_HEADER = "x-model-ref"
"""Where the caller names the ref. A header rather than a query parameter, so it cannot collide
with Cube's own and there is nothing to strip before forwarding."""

TOKEN_TTL_S = 600
MIN_SECRET_BYTES = 32
LOCAL_CLAIMS = {"user_id": "local"}
"""Besides `ref`. Cube's `security_context` is also the seam row-level security would read."""

TIMEOUT_S = 120.0
"""A warehouse query, not a metadata read. Longer than the store's own calls for that reason."""


def mint(ref: str) -> str:
    """Sign the security context Cube requires. Refusing a short secret is louder than pyjwt
    warning on every request."""
    if len(API_SECRET.encode()) < MIN_SECRET_BYTES:
        raise ValueError(
            f"CUBEJS_API_SECRET is {len(API_SECRET.encode())} bytes; HS256 needs at least "
            f"{MIN_SECRET_BYTES}"
        )
    return jwt.encode({**LOCAL_CLAIMS, "ref": ref}, API_SECRET, algorithm="HS256")


async def forward(
    method: str, path: str, ref: str, *, body: bytes = b"", query: str = ""
) -> httpx.Response:
    """One request to Cube, signed for `ref`. Returns Cube's response as it came.

    Its own client per call rather than a shared one: a query can take two minutes and the store
    is otherwise answering small requests, so a pool sized for one is the wrong pool for the
    other. Cheap next to the query itself.
    """
    url = f"{CUBE_URL}/{path.lstrip('/')}"
    headers = {"Authorization": mint(ref)}
    if body:
        headers["content-type"] = "application/json"
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        return await client.request(
            method, url, params=query or None, content=body or None, headers=headers
        )
