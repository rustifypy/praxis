import httpx
import pytest
import respx
import yaml

from analyst_agent.sources import compose, health, registry
from analyst_agent.sources.drivers import get
from analyst_agent.sources.registry import Source

PG = Source(name="shop", description="Orders.", driver="postgres", port=4001)
DBX = Source(name="events", description="Clickstream.", driver="databricks-jdbc", port=4002)
BQ = Source(
    name="ads",
    description="Ad spend.",
    driver="bigquery",
    port=4003,
    mounts={"/cube/conf/bq-key.json": "/home/me/key.json"},
)


def test_committed_compose_is_in_sync_with_registry():
    """docker-compose.sources.yml says it is generated — this proves it still is."""
    root = registry.project_root()
    committed = (root / registry.COMPOSE_FILE).read_text()
    assert committed == compose.render(registry.load(root)), (
        "docker-compose.sources.yml drifted from sources.yml — run `analyst source sync`"
    )


def test_service_carries_model_mount_env_file_and_port():
    svc = compose.render([PG])
    doc = yaml.safe_load(svc)["services"]["cube-shop"]
    assert doc["ports"] == ["4001:4000"]
    assert doc["env_file"] == [".env.sources/shop.env"]
    assert doc["volumes"] == ["./cube/sources/shop:/cube/conf/model"]
    assert doc["depends_on"] == {"ds-cubestore-router": {"condition": "service_started"}}
    assert "platform" not in doc


def test_databricks_gets_the_jdk_image_and_amd64_platform():
    doc = yaml.safe_load(compose.render([DBX]))["services"]["cube-events"]
    assert doc["image"] == "cubejs/cube:jdk"
    assert doc["platform"] == "linux/amd64"


def test_key_file_is_mounted_read_only():
    doc = yaml.safe_load(compose.render([BQ]))["services"]["cube-ads"]
    assert "/home/me/key.json:/cube/conf/bq-key.json:ro" in doc["volumes"]


def test_cubestore_is_shared_by_every_source():
    services = yaml.safe_load(compose.render([PG, DBX]))["services"]
    assert set(services) == {
        "cube-shop",
        "cube-events",
        *("ds-cubestore-router", "ds-cubestore-worker"),
    }


def test_save_load_round_trip_preserves_mounts(tmp_path):
    (tmp_path / "pyproject.toml").touch()
    registry.save([PG, BQ], root=tmp_path)
    loaded = registry.load(root=tmp_path)
    assert [s.name for s in loaded] == ["shop", "ads"]
    assert loaded[1].mounts == {"/cube/conf/bq-key.json": "/home/me/key.json"}
    assert "mounts" not in yaml.safe_load((tmp_path / "sources.yml").read_text())["sources"][0]


def test_env_file_is_owner_only_and_holds_the_driver_type(tmp_path):
    values = {
        "CUBEJS_DB_HOST": "db.internal",
        "CUBEJS_DB_PORT": "5432",
        "CUBEJS_DB_NAME": "analytics",
        "CUBEJS_DB_USER": "cube",
        "CUBEJS_DB_PASS": "secret",
        "CUBEJS_DB_SSL": "true",
    }
    path = registry.write_env(PG, values, root=tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    written = registry.read_env(PG, root=tmp_path)
    assert written["CUBEJS_DB_TYPE"] == "postgres"
    assert written == {"CUBEJS_DB_TYPE": "postgres", **values}


def test_fixed_fields_are_written_without_being_asked_for(tmp_path):
    registry.write_env(DBX, {"CUBEJS_DB_DATABRICKS_URL": "jdbc://x"}, root=tmp_path)
    written = registry.read_env(DBX, root=tmp_path)
    assert written["CUBEJS_DB_DATABRICKS_ACCEPT_POLICY"] == "true"


def test_scaffold_creates_the_dirs_cube_mounts(tmp_path):
    base = registry.scaffold_model_dir(PG, root=tmp_path)
    assert (base / "cubes").is_dir() and (base / "views").is_dir()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("", "required"),
        ("Shop", "lowercase"),
        ("9shop", "lowercase letters"),
        ("my_shop", "lowercase letters"),
        ("my-shop", None),
    ],
)
def test_validate_name(name, expected, tmp_path):
    (tmp_path / "pyproject.toml").touch()
    error = registry.validate_name(name, root=tmp_path)
    if expected is None:
        assert error is None
    else:
        assert error and expected in error


def test_validate_name_rejects_duplicates(tmp_path):
    (tmp_path / "pyproject.toml").touch()
    registry.save([PG], root=tmp_path)
    assert "already exists" in registry.validate_name("shop", root=tmp_path)


def test_next_port_skips_assigned_ports(monkeypatch):
    monkeypatch.setattr(registry, "port_in_use", lambda port: False)
    assert registry.next_port([PG, DBX, BQ]) == 4004
    assert registry.next_port([]) == registry.FIRST_PORT


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
