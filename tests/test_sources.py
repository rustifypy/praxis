import httpx
import respx

from analyst_agent.sources import health, registry
from analyst_agent.sources.drivers import get
from analyst_agent.sources.registry import Source

PG = Source(name="shop", description="Orders.", driver="postgres", port=4001)


def test_scaffold_creates_the_dirs_cube_mounts(tmp_path):
    base = registry.scaffold_model_dir(PG, root=tmp_path)
    assert (base / "cubes").is_dir() and (base / "views").is_dir()


@respx.mock
async def test_health_ok():
    respx.get("http://localhost:4001/readyz").mock(
        return_value=httpx.Response(200, json={"health": "HEALTH"})
    )
    result = await health.check(PG)
    assert result.ok and result.status == "HEALTH"


@respx.mock
async def test_health_reports_down_with_detail():
    respx.get("http://localhost:4001/readyz").mock(
        return_value=httpx.Response(500, json={"health": "DOWN"})
    )
    result = await health.check(PG)
    assert not result.ok
    assert result.status == "DOWN"
    assert "connection is down" in result.detail


@respx.mock
async def test_health_survives_a_connection_reset_while_booting():
    """A container that accepts then resets raises ReadError, not ConnectError."""
    respx.get("http://localhost:4001/readyz").mock(side_effect=httpx.ReadError("reset"))
    result = await health.check(PG)
    assert not result.ok
    assert result.status == "unreachable"
    assert "ReadError" in result.detail


@respx.mock
async def test_wait_until_ready_gives_up_after_repeated_down():
    respx.get("http://localhost:4001/readyz").mock(
        return_value=httpx.Response(500, json={"health": "DOWN"})
    )
    result, attempts = await health.wait_until_ready(PG, attempts=8, every=0, down_tolerance=1)
    assert not result.ok and attempts == 2


def test_every_driver_exposes_a_cube_db_type():
    for key in ("postgres", "mysql", "snowflake", "bigquery", "databricks-jdbc"):
        spec = get(key)
        assert spec.key == key
        assert spec.fields
