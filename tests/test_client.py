import httpx
import respx

from analyst_agent.cube import gate
from analyst_agent.cube.client import CubeClient

ROOT = "http://cube.test/cubejs-api"


def client() -> CubeClient:
    return CubeClient(token="test-token", root=ROOT)


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
