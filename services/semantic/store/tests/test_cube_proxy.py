"""Cube behind this service — the only door into the deployment.

What matters here is the seam, not Cube: that a caller cannot query without the write token,
that the ref it names is what gets signed, and that Cube's own answer comes back untouched. What
Cube does with a query is Cube's suite and `make store-check`.
"""

import os
import tempfile

import httpx
import jwt
import pytest

os.environ.setdefault("MODEL_STORE_READ_TOKEN", "read-tok")
os.environ.setdefault("MODEL_STORE_WRITE_TOKEN", "write-tok")
os.environ.setdefault("CUBEJS_API_SECRET", "s" * 40)
os.environ.setdefault("CUBE_URL", "http://cube.test:4000")
os.environ.setdefault("MODEL_STORE_PATH", f"{tempfile.mkdtemp()}/model.git")

from model_store import cube, repo  # noqa: E402
from model_store.app import ROOT, app  # noqa: E402

WRITE = {"Authorization": "Bearer write-tok"}
READ = {"Authorization": "Bearer read-tok"}


@pytest.fixture
async def api():
    await repo.ensure(ROOT)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://s") as client:
        yield client


@pytest.fixture
def forwarded(monkeypatch):
    """Cube replaced by a recorder. Returns the list of calls it saw."""
    seen: list[dict] = []

    async def forward(method, path, ref, *, body=b"", query=""):
        seen.append({"method": method, "path": path, "ref": ref, "body": body, "query": query})
        return httpx.Response(200, json={"cubes": []})

    monkeypatch.setattr(cube, "forward", forward)
    return seen


# --- who may query ------------------------------------------------------------


async def test_no_token_cannot_query(api, forwarded):
    assert (await api.get("/cube/cubejs-api/v1/meta")).status_code == 401
    assert forwarded == []


async def test_the_read_token_cannot_query(api, forwarded):
    """It is the token handed to Cube itself. Letting it query back through this proxy would
    make the credential given away as capable as the one kept."""
    assert (await api.get("/cube/cubejs-api/v1/meta", headers=READ)).status_code == 401
    assert forwarded == []


async def test_the_write_token_can(api, forwarded):
    assert (await api.get("/cube/cubejs-api/v1/meta", headers=WRITE)).status_code == 200
    assert forwarded[0]["path"] == "cubejs-api/v1/meta"


# --- the ref ------------------------------------------------------------------


async def test_the_ref_named_in_the_header_is_the_one_signed(api, forwarded):
    await api.get("/cube/cubejs-api/v1/meta", headers={**WRITE, cube.REF_HEADER: "edit-sales"})
    assert forwarded[0]["ref"] == "edit-sales"


async def test_no_ref_header_means_main(api, forwarded):
    """A request that forgot it must not be answered from whatever was asked for last."""
    await api.get("/cube/cubejs-api/v1/meta", headers=WRITE)
    assert forwarded[0]["ref"] == "main"


def test_the_ref_reaches_cube_as_a_security_context_claim():
    """`cube.js` reads `securityContext.ref`, so the claim name is load-bearing — a rename here
    silently serves `main` for every ref."""
    claims = jwt.decode(cube.mint("edit-sales"), cube.API_SECRET, algorithms=["HS256"])
    assert claims["ref"] == "edit-sales"
    assert claims["user_id"] == "local"


def test_a_secret_too_short_to_sign_with_is_refused(monkeypatch):
    """HS256's key must be at least as long as its output. Refusing is louder than pyjwt warning
    on every single request."""
    monkeypatch.setattr(cube, "API_SECRET", "too-short")
    with pytest.raises(ValueError, match="at least 32"):
        cube.mint("main")


# --- pass-through -------------------------------------------------------------


async def test_the_path_is_forwarded_verbatim(api, forwarded):
    """Nothing here knows that /v1/load is a query and /readyz is a health check. One less thing
    to get wrong when Cube's routing changes."""
    await api.get("/cube/readyz", headers=WRITE)
    assert forwarded[0]["path"] == "readyz"


async def test_a_body_and_a_query_string_both_survive(api, forwarded):
    await api.post(
        "/cube/cubejs-api/v1/load?nonce=7", headers=WRITE, json={"measures": ["a.count"]}
    )
    assert forwarded[0]["method"] == "POST"
    assert b"a.count" in forwarded[0]["body"]
    assert forwarded[0]["query"] == "nonce=7"


async def test_cubes_own_status_and_body_come_back(api, monkeypatch):
    """A query Cube refuses is a 400 with a body the agent can act on. Turning that into a 502
    would take the reasons away from the one thing that can use them."""

    async def refuse(method, path, ref, *, body=b"", query=""):
        return httpx.Response(400, json={"error": "Member not found: orders.nope"})

    monkeypatch.setattr(cube, "forward", refuse)
    answer = await api.post("/cube/cubejs-api/v1/load", headers=WRITE, json={})
    assert answer.status_code == 400
    assert "orders.nope" in answer.text


async def test_a_cube_that_is_not_answering_is_a_502(api, monkeypatch):
    """Distinguishable from a refusal: one is "unreachable" and the other is "no such member",
    and only the second is worth showing the agent."""

    async def down(method, path, ref, *, body=b"", query=""):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(cube, "forward", down)
    answer = await api.post("/cube/cubejs-api/v1/load", headers=WRITE, json={})
    assert answer.status_code == 502
    assert "did not answer" in answer.text


async def test_the_model_routes_still_work_beside_it(api):
    """`/cube/{path:path}` is broad, and a route that swallowed `/refs` or `/files` would take
    the model API down with it."""
    assert (await api.get("/refs", headers=READ)).status_code == 200
    assert (await api.get("/health")).status_code == 200
