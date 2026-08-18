import httpx
import respx

from analyst_agent.sources import health, registry
from analyst_agent.sources.drivers import get


def test_scaffold_creates_the_dirs_cube_mounts(tmp_path):
    base = registry.scaffold_dirs(root=tmp_path)
    assert (base / "cubes").is_dir() and (base / "views").is_dir()


@respx.mock
async def test_health_ok():
    respx.get("http://localhost:4000/readyz").mock(
        return_value=httpx.Response(200, json={"health": "HEALTH"})
    )
    result = await health.check()
    assert result.ok and result.status == "HEALTH"


@respx.mock
async def test_health_reports_down_with_detail():
    respx.get("http://localhost:4000/readyz").mock(
        return_value=httpx.Response(500, json={"health": "DOWN"})
    )
    result = await health.check()
    assert not result.ok
    assert result.status == "DOWN"
    assert "connection is down" in result.detail


@respx.mock
async def test_health_survives_a_connection_reset_while_booting():
    """A container that accepts then resets raises ReadError, not ConnectError."""
    respx.get("http://localhost:4000/readyz").mock(side_effect=httpx.ReadError("reset"))
    result = await health.check()
    assert not result.ok
    assert result.status == "unreachable"
    assert "ReadError" in result.detail


@respx.mock
async def test_wait_until_ready_gives_up_after_repeated_down():
    respx.get("http://localhost:4000/readyz").mock(
        return_value=httpx.Response(500, json={"health": "DOWN"})
    )
    result, attempts = await health.wait_until_ready(attempts=8, every=0, down_tolerance=1)
    assert not result.ok and attempts == 2


SERVED = health.Serving(cubes=13, views=["sales"], described=13)


def test_a_stopped_container_and_an_uncompilable_model_are_not_the_same_state():
    """Both leave `serving` null, so anything reading only that has to call one of them by the
    other's name — "no model yet" for a source with thirteen cubes is wrong, not vague."""
    assert health.serving_state(running=False, healthy=False, served=None) == "stopped"
    assert health.serving_state(running=True, healthy=True, served=None) == "broken"


def test_a_container_that_is_up_but_not_answering_is_its_own_state():
    assert health.serving_state(running=True, healthy=False, served=None) == "unreachable"


def test_cube_answering_with_no_cubes_is_a_model_not_built_yet():
    """Distinct from `broken`: Cube is fine, there is simply no model in the mount."""
    empty = health.Serving(cubes=0, views=[], described=0)
    assert health.serving_state(running=True, healthy=True, served=empty) == "empty"
    assert health.serving_state(running=True, healthy=True, served=SERVED) == "ok"


def test_every_state_is_declared():
    """SERVING_STATES is what the browser's copy is keyed on, so a new one must be added there
    rather than falling through to undefined."""
    states = {
        health.serving_state(running=r, healthy=h, served=s)
        for r in (True, False)
        for h in (True, False)
        for s in (None, SERVED, health.Serving(cubes=0, views=[], described=0))
    }
    assert states <= set(health.SERVING_STATES)
    assert states == set(health.SERVING_STATES)


def test_every_driver_exposes_a_cube_db_type():
    for key in ("postgres", "mysql", "snowflake", "bigquery", "databricks-jdbc"):
        spec = get(key)
        assert spec.key == key
        assert spec.fields
