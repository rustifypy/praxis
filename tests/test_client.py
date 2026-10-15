import asyncio

import httpx
import pytest
import respx

from praxis.modelstore import gate
from praxis.modelstore.client import Bound, ModelStore, StoreError

ROOT = "http://store.test"
"""Cube is reached only through the store, and the ref is a path segment there."""

REFS = f"{ROOT}/refs/main"
META_URL, SQL_URL, LOAD_URL = f"{REFS}/meta", f"{REFS}/sql", f"{REFS}/query"
TOKEN = "w" * 40
META = {"cubes": [{"name": "orders", "type": "cube", "measures": [{"name": "orders.count"}]}]}


def store(token: str | None = TOKEN) -> ModelStore:
    return ModelStore(ROOT, token)


def client(ref: str = "main") -> Bound:
    """The session asked about one ref — what everything that queries is handed."""
    return store().at(ref)


# --- the session ------------------------------------------------------------


@respx.mock
async def test_every_request_carries_the_token_and_names_the_ref_in_the_path():
    """The whole reason this module exists: six call sites reached Cube with no Authorization
    header at all. The ref is the route the store mints its Cube token for, so a request to the
    wrong one would silently be answered from `main`."""
    edited = f"{ROOT}/refs/edit-sales"
    meta = respx.get(f"{edited}/meta").mock(return_value=httpx.Response(200, json=META))
    load = respx.post(f"{edited}/query").mock(return_value=httpx.Response(200, json={"data": []}))
    async with store() as opened:
        cube = opened.at("edit-sales")
        await cube.catalog()
        await cube.load(gate.parse({"measures": ["orders.count"]})[0])
    for route in (meta, load):
        assert route.calls[0].request.headers["authorization"] == f"Bearer {TOKEN}"


@respx.mock
async def test_no_secret_is_the_only_unauthenticated_path():
    """The probe's container is started with a 5-byte secret, so it cannot be signed for."""
    route = respx.get(META_URL).mock(return_value=httpx.Response(200, json=META))
    async with store(None) as opened:
        await opened.at().catalog()
    assert "authorization" not in route.calls[0].request.headers


def test_no_token_is_a_first_run_rather_than_a_mistake():
    """Nothing is minted here any more — the store signs Cube's token, so a short secret is its
    error to raise and this side only carries a bearer token."""

    async def unauthenticated():
        async with store(None) as opened:
            assert "authorization" not in opened._headers()
            assert opened.at().ref == "main"

    asyncio.run(unauthenticated())


@respx.mock
async def test_an_unset_secret_is_a_first_run_not_a_misconfiguration():
    """A blank one must not stop the portal booting — it is the screen you would fix it on."""
    respx.get(META_URL).mock(return_value=httpx.Response(200, json=META))
    async with store("") as opened:
        assert await opened.at().catalog()


@respx.mock
async def test_the_catalog_is_read_once_and_refreshed_on_request():
    route = respx.get(META_URL).mock(return_value=httpx.Response(200, json=META))
    async with store() as opened:
        cube = opened.at()
        first = await cube.catalog()
        assert await cube.catalog() is first
        assert route.call_count == 1
        await cube.catalog(refresh=True)
        assert route.call_count == 2


@respx.mock
async def test_a_stopped_container_is_a_cube_error_not_a_transport_error():
    """`httpx.RequestError` does not subclass OSError, and letting it out is what 500'd the
    dashboard page. `from exc` is what keeps the original without a field holding it."""
    respx.get(META_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(StoreError) as caught:
        await client().catalog()
    assert isinstance(caught.value.__cause__, httpx.ConnectError)


@respx.mock
async def test_cube_answering_badly_carries_what_it_said():
    respx.get(META_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(StoreError, match="boom"):
        await client().catalog()


@respx.mock
async def test_waiting_for_a_compile_polls_past_a_booting_container():
    respx.get(META_URL).mock(
        side_effect=[httpx.ConnectError("booting"), httpx.Response(200, json=META)]
    )
    async with store() as opened:
        catalog = await opened.at().wait_until_compiled(every=0)
    assert [e.name for e in catalog.entities] == ["orders"]


@respx.mock
async def test_a_compile_error_ends_the_wait_rather_than_burning_the_deadline():
    """Cube names the offending file, and waiting out 90s would throw that away."""
    respx.get(META_URL).mock(
        return_value=httpx.Response(200, json={"error": "Compile errors: Unexpected YAML key"})
    )
    with pytest.raises(StoreError, match="Unexpected YAML key"):
        await client().wait_until_compiled(every=0)


@respx.mock
async def test_compile_returns_sql():
    respx.post(SQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"sql": {"sql": ["SELECT count(*) FROM orders WHERE status = $1", ["paid"]]}},
        )
    )
    result = await client().compile(
        gate.parse({"measures": ["orders.count"]})[0],
    )
    assert result.ok
    assert result.sql.startswith("SELECT count(*)")
    assert result.params == ["paid"]


@respx.mock
async def test_compile_failure_is_not_raised():
    respx.post(SQL_URL).mock(
        return_value=httpx.Response(400, json={"error": "Member not found: orders.nope"})
    )
    result = await client().compile(gate.parse({"measures": ["orders.count"]})[0])
    assert not result.ok
    assert "Member not found" in result.error


@respx.mock
async def test_gate_run_happy_path(catalog):
    respx.post(SQL_URL).mock(
        return_value=httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    )
    respx.post(LOAD_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"orders.count": 42}], "annotation": {}, "requestId": "req-1"},
        )
    )
    result = await gate.run({"measures": ["orders.count"]}, catalog, client(), max_rows=100)
    assert result.ok
    assert result.rows == [{"orders.count": 42}]
    assert result.sql == "SELECT 1"


@respx.mock
async def test_a_200_carrying_an_error_is_not_an_empty_result():
    """Cube reports some failures as 200 with an `error` key and no `data`, so reading rows
    off the body turns a failed query into one that matched nothing."""
    respx.post(LOAD_URL).mock(
        return_value=httpx.Response(200, json={"error": "Values required for filter orders.status"})
    )
    with pytest.raises(StoreError, match="Values required"):
        await client().load(gate.parse({"measures": ["orders.count"]})[0])


@respx.mock
async def test_the_gate_reports_a_200_error_as_a_failed_execution(catalog):
    """The whole point of raising: `execute` already turns a StoreError into a refusal the
    agent can act on, where empty rows would have been answered as fact."""
    respx.post(SQL_URL).mock(
        return_value=httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    )
    respx.post(LOAD_URL).mock(
        return_value=httpx.Response(200, json={"error": "Primary key not found"})
    )
    result = await gate.run({"measures": ["orders.count"]}, catalog, client(), max_rows=100)
    assert not result.ok
    assert "Primary key not found" in result.errors[0]
    assert result.rows is None


@respx.mock
async def test_gate_stops_before_compile_on_bad_member(catalog):
    route = respx.post(SQL_URL)
    result = await gate.run({"measures": ["orders.nope"]}, catalog, client(), max_rows=100)
    assert not result.ok
    assert not route.called  # tier 2 is free — no network call made


@respx.mock
async def test_max_rows_caps_limit(catalog):
    respx.post(SQL_URL).mock(
        return_value=httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    )
    load = respx.post(LOAD_URL).mock(
        return_value=httpx.Response(200, json={"data": [], "annotation": {}})
    )
    await gate.run(
        {"measures": ["orders.count"], "limit": 40_000, "order": {"orders.count": "desc"}},
        catalog,
        client(),
        max_rows=500,
    )
    import json as _json

    sent = _json.loads(load.calls[0].request.content)
    assert sent["query"]["limit"] == 500
