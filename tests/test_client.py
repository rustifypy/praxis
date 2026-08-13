import httpx
import pytest
import respx

from analyst_agent.cube import gate
from analyst_agent.cube.client import CubeClient, CubeError, mint_token

ROOT = "http://cube.test/cubejs-api"
SECRET = "s" * 32
META = {"cubes": [{"name": "orders", "type": "cube", "measures": [{"name": "orders.count"}]}]}


def client() -> CubeClient:
    return CubeClient(ROOT, SECRET)


# --- the session ------------------------------------------------------------


@respx.mock
async def test_every_request_carries_a_token():
    """The whole reason this module exists: six call sites reached Cube with no Authorization
    header at all, and only dev mode made that work."""
    meta = respx.get(f"{ROOT}/v1/meta").mock(return_value=httpx.Response(200, json=META))
    load = respx.post(f"{ROOT}/v1/load").mock(return_value=httpx.Response(200, json={"data": []}))
    async with client() as cube:
        await cube.catalog()
        await cube.load(gate.parse({"measures": ["orders.count"]})[0])
    for route in (meta, load):
        assert route.calls[0].request.headers["authorization"].startswith("ey")


@respx.mock
async def test_no_secret_is_the_only_unauthenticated_path():
    """The probe's container is started with a 5-byte secret, so it cannot be signed for."""
    route = respx.get(f"{ROOT}/v1/meta").mock(return_value=httpx.Response(200, json=META))
    async with CubeClient(ROOT, None) as cube:
        await cube.catalog()
    assert "authorization" not in route.calls[0].request.headers


def test_a_short_secret_is_refused_at_construction():
    """Rather than per request, where it raised out of whichever query happened to be first."""
    with pytest.raises(ValueError, match="at least 32"):
        CubeClient(ROOT, "too-short")
    with pytest.raises(ValueError, match="at least 32"):
        mint_token({"user_id": "local"}, "too-short")


@respx.mock
async def test_an_unset_secret_is_a_first_run_not_a_misconfiguration():
    """A blank one must not stop the portal booting — it is the screen you would fix it on."""
    respx.get(f"{ROOT}/v1/meta").mock(return_value=httpx.Response(200, json=META))
    async with CubeClient(ROOT, "") as cube:
        assert await cube.catalog()


@respx.mock
async def test_the_catalog_is_read_once_and_refreshed_on_request():
    route = respx.get(f"{ROOT}/v1/meta").mock(return_value=httpx.Response(200, json=META))
    async with client() as cube:
        first = await cube.catalog()
        assert await cube.catalog() is first
        assert route.call_count == 1
        await cube.catalog(refresh=True)
        assert route.call_count == 2


@respx.mock
async def test_a_stopped_container_is_a_cube_error_not_a_transport_error():
    """`httpx.RequestError` does not subclass OSError, and letting it out is what 500'd the
    dashboard page. Callers catch CubeError alone now."""
    respx.get(f"{ROOT}/v1/meta").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(CubeError) as caught:
        await client().catalog()
    assert caught.value.unreachable
    assert caught.value.status is None


@respx.mock
async def test_cube_answering_badly_is_not_reported_as_unreachable():
    """`health.serving` tells a stopped container from one serving no model."""
    respx.get(f"{ROOT}/v1/meta").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(CubeError) as caught:
        await client().catalog()
    assert not caught.value.unreachable
    assert caught.value.status == 500


@respx.mock
async def test_waiting_for_a_compile_polls_past_a_booting_container():
    respx.get(f"{ROOT}/v1/meta").mock(
        side_effect=[httpx.ConnectError("booting"), httpx.Response(200, json=META)]
    )
    async with client() as cube:
        catalog = await cube.wait_until_compiled(every=0)
    assert [e.name for e in catalog.entities] == ["orders"]


@respx.mock
async def test_a_compile_error_ends_the_wait_rather_than_burning_the_deadline():
    """Cube names the offending file, and waiting out 90s would throw that away."""
    respx.get(f"{ROOT}/v1/meta").mock(
        return_value=httpx.Response(200, json={"error": "Compile errors: Unexpected YAML key"})
    )
    with pytest.raises(CubeError, match="Unexpected YAML key"):
        await client().wait_until_compiled(every=0)


@respx.mock
async def test_compile_returns_sql():
    respx.post(f"{ROOT}/v1/sql").mock(
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
    respx.post(f"{ROOT}/v1/sql").mock(
        return_value=httpx.Response(400, json={"error": "Member not found: orders.nope"})
    )
    result = await client().compile(gate.parse({"measures": ["orders.count"]})[0])
    assert not result.ok
    assert "Member not found" in result.error


@respx.mock
async def test_gate_run_happy_path(catalog):
    respx.post(f"{ROOT}/v1/sql").mock(
        return_value=httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    )
    respx.post(f"{ROOT}/v1/load").mock(
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
    respx.post(f"{ROOT}/v1/load").mock(
        return_value=httpx.Response(200, json={"error": "Values required for filter orders.status"})
    )
    with pytest.raises(CubeError, match="Values required"):
        await client().load(gate.parse({"measures": ["orders.count"]})[0])


@respx.mock
async def test_the_gate_reports_a_200_error_as_a_failed_execution(catalog):
    """The whole point of raising: `execute` already turns a CubeError into a refusal the
    agent can act on, where empty rows would have been answered as fact."""
    respx.post(f"{ROOT}/v1/sql").mock(
        return_value=httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    )
    respx.post(f"{ROOT}/v1/load").mock(
        return_value=httpx.Response(200, json={"error": "Primary key not found"})
    )
    result = await gate.run({"measures": ["orders.count"]}, catalog, client(), max_rows=100)
    assert not result.ok
    assert "Primary key not found" in result.errors[0]
    assert result.rows is None


@respx.mock
async def test_gate_stops_before_compile_on_bad_member(catalog):
    route = respx.post(f"{ROOT}/v1/sql")
    result = await gate.run({"measures": ["orders.nope"]}, catalog, client(), max_rows=100)
    assert not result.ok
    assert not route.called  # tier 2 is free — no network call made


@respx.mock
async def test_max_rows_caps_limit(catalog):
    respx.post(f"{ROOT}/v1/sql").mock(
        return_value=httpx.Response(200, json={"sql": {"sql": ["SELECT 1", []]}})
    )
    load = respx.post(f"{ROOT}/v1/load").mock(
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
