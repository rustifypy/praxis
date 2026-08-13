"""The `docker run` argument list, asserted without a daemon.

These containers are the only thing standing between a row in sources.db and a
working source, and the argv is the whole contract — so it is a pure function and
this is where it gets checked.
"""

import pathlib

import yaml

from analyst_agent.sources import docker
from analyst_agent.sources.drivers import (
    ConnField,
    Driver,
    credentials_for,
    get,
    missing_fields,
    required_envs,
    split_mounts,
)
from analyst_agent.sources.registry import Source

SHOP = Source(
    name="shop",
    description="Orders.",
    driver="postgres",
    port=4001,
    credentials={
        "CUBEJS_DB_TYPE": "postgres",
        "CUBEJS_DB_HOST": "host.docker.internal",
        "CUBEJS_DB_PORT": "5432",
        "CUBEJS_DB_NAME": "shop",
        "CUBEJS_DB_USER": "cube",
        "CUBEJS_DB_PASS": "secret",
    },
)

DBX = Source(
    name="events",
    description="Clickstream.",
    driver="databricks-jdbc",
    port=4002,
    credentials={"CUBEJS_DB_DATABRICKS_URL": "jdbc://x", "CUBEJS_DB_DATABRICKS_TOKEN": "t"},
)

BQ = Source(
    name="ads",
    description="Ad spend.",
    driver="bigquery",
    port=4003,
    mounts={"/cube/conf/bq-key.json": "/home/me/key.json"},
    credentials={"CUBEJS_DB_BQ_PROJECT_ID": "acme"},
)


def env_of(args: list[str]) -> dict[str, str]:
    pairs = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    return dict(p.partition("=")[::2] for p in pairs)


def test_argv_publishes_the_port_and_mounts_the_model():
    args = docker.argv(SHOP, "/repo/cube/sources/shop", api_secret="x" * 32)
    assert args[:4] == ["run", "-d", "--name", "cube-shop"]
    assert "/repo/cube/sources/shop:/cube/conf/model" in args
    assert args[-1].startswith("cubejs/cube:")


def test_the_port_is_bound_to_loopback_only():
    """Dev mode skips JWT verification, so a 0.0.0.0 bind would answer the network."""
    args = docker.argv(SHOP, "/m", api_secret="x" * 32)
    assert args[args.index("-p") + 1] == "127.0.0.1:4001:4000"


def test_argv_restarts_with_docker_because_nothing_else_starts_a_source():
    args = docker.argv(SHOP, "/m", api_secret="x" * 32)
    assert args[args.index("--restart") + 1] == "unless-stopped"


def test_argv_joins_the_shared_network_so_cubestore_resolves():
    args = docker.argv(SHOP, "/m", api_secret="x" * 32)
    assert args[args.index("--network") + 1] == "analyst-net"
    env = env_of(args)
    assert env["CUBEJS_CUBESTORE_HOST"] == "ds-cubestore-router"
    assert env["CUBEJS_CUBESTORE_PORT"] == "3030"


def test_argv_carries_the_credentials_and_the_api_secret():
    env = env_of(docker.argv(SHOP, "/m", api_secret="s" * 32))
    assert env["CUBEJS_DB_PASS"] == "secret"
    assert env["CUBEJS_DB_TYPE"] == "postgres"
    assert env["CUBEJS_API_SECRET"] == "s" * 32


def test_dev_mode_is_off_unless_asked_for():
    """It skips JWT verification, so it must never be on by accident."""
    assert env_of(docker.argv(SHOP, "/m", api_secret="x" * 32))["CUBEJS_DEV_MODE"] == "false"
    args = docker.argv(SHOP, "/m", api_secret="x" * 32, dev_mode=True)
    assert env_of(args)["CUBEJS_DEV_MODE"] == "true"


def test_databricks_gets_the_jdk_image_and_the_amd64_platform():
    args = docker.argv(DBX, "/m", api_secret="x" * 32)
    assert args[-1] == "cubejs/cube:jdk"
    assert args[args.index("--platform") + 1] == "linux/amd64"
    # Not optional — the JDBC driver refuses to load without it.
    assert env_of(args)["CUBEJS_DB_DATABRICKS_ACCEPT_POLICY"] == "true"


def test_a_key_file_is_mounted_read_only():
    args = docker.argv(BQ, "/m", api_secret="x" * 32)
    assert "/home/me/key.json:/cube/conf/bq-key.json:ro" in args


def test_the_image_tag_follows_cube_tag(monkeypatch):
    monkeypatch.setenv("CUBE_TAG", "v1.2.3")
    assert docker.argv(SHOP, "/m", api_secret="x" * 32)[-1] == "cubejs/cube:v1.2.3"
    # A driver with a pinned tag ignores it — it needs that specific build.
    assert docker.argv(DBX, "/m", api_secret="x" * 32)[-1] == "cubejs/cube:jdk"


# --- credentials from a driver spec -----------------------------------------


def test_fixed_fields_are_added_without_being_supplied():
    creds = credentials_for(get("databricks-jdbc"), {"CUBEJS_DB_DATABRICKS_URL": "jdbc://x"})
    assert creds["CUBEJS_DB_DATABRICKS_ACCEPT_POLICY"] == "true"
    assert creds["CUBEJS_DB_TYPE"] == "databricks-jdbc"


def test_empty_optional_fields_are_dropped_not_passed_as_empty():
    """`-e KEY=` and an unset KEY are not the same to every driver."""
    creds = credentials_for(
        get("snowflake"), {"CUBEJS_DB_USER": "u", "CUBEJS_DB_SNOWFLAKE_ROLE": ""}
    )
    assert "CUBEJS_DB_SNOWFLAKE_ROLE" not in creds
    assert creds["CUBEJS_DB_USER"] == "u"


def test_missing_required_fields_are_named():
    missing = missing_fields(get("postgres"), {"CUBEJS_DB_HOST": "h", "CUBEJS_DB_PORT": "5432"})
    assert "CUBEJS_DB_NAME" in missing
    assert "CUBEJS_DB_USER" in missing
    # Optional, so its absence is not an error.
    assert "CUBEJS_DB_SSL" not in missing


def test_a_required_boolean_is_still_required():
    """A boolean is a field like any other: it has to be answered. The form reads
    `required_envs` rather than deciding for itself, so it cannot exempt a class of field."""
    spec = Driver(key="x", label="X", fields=(ConnField("FLAG", "Flag", boolean=True),))
    assert required_envs(spec) == ["FLAG"]
    assert missing_fields(spec, {}) == ["FLAG"]
    assert missing_fields(spec, {"FLAG": "false"}) == []


def test_a_fixed_field_is_nobody_to_fill_in():
    """Databricks' accept-policy flag. It is not offered by /drivers either, so the form has
    nothing to leave blank — but only this list says so."""
    assert "CUBEJS_DB_DATABRICKS_ACCEPT_POLICY" not in required_envs(get("databricks-jdbc"))
    assert "CUBEJS_DB_DATABRICKS_URL" in required_envs(get("databricks-jdbc"))


def test_missing_fields_is_required_envs_with_the_blanks_kept():
    spec = get("postgres")
    assert missing_fields(spec, {}) == required_envs(spec)


def test_nothing_is_missing_once_every_required_field_has_a_value():
    values = {
        "CUBEJS_DB_HOST": "h",
        "CUBEJS_DB_PORT": "5432",
        "CUBEJS_DB_NAME": "n",
        "CUBEJS_DB_USER": "u",
        "CUBEJS_DB_PASS": "p",
    }
    assert missing_fields(get("postgres"), values) == []


# --- host paths become mounts ------------------------------------------------


def test_a_key_file_field_becomes_a_mount_and_the_credential_is_the_container_path():
    """The container is never told the host path — it is told where the file is mounted."""
    values, mounts = split_mounts(
        get("bigquery"),
        {"CUBEJS_DB_BQ_PROJECT_ID": "proj", "CUBEJS_DB_BQ_KEY_FILE": "/keys/sa.json"},
    )
    assert mounts == {"/cube/conf/bq-key.json": "/keys/sa.json"}
    assert values["CUBEJS_DB_BQ_KEY_FILE"] == "/cube/conf/bq-key.json"
    assert values["CUBEJS_DB_BQ_PROJECT_ID"] == "proj"
    # And that container path is what reaches the container.
    assert credentials_for(get("bigquery"), values)["CUBEJS_DB_BQ_KEY_FILE"] == (
        "/cube/conf/bq-key.json"
    )


def test_a_tilde_in_a_key_file_path_is_expanded():
    """docker gets an absolute path or bind-mounts a directory named `~`."""
    _, mounts = split_mounts(get("bigquery"), {"CUBEJS_DB_BQ_KEY_FILE": "~/sa.json"})
    assert mounts["/cube/conf/bq-key.json"] == str(pathlib.Path.home() / "sa.json")


def test_a_blank_key_file_field_produces_no_mount():
    """Blank means "unchanged" on an edit, so it must not mount an empty path."""
    values, mounts = split_mounts(get("bigquery"), {"CUBEJS_DB_BQ_KEY_FILE": "  "})
    assert mounts == {}
    # Left as typed rather than rewritten to the container path, so `merge_credentials`
    # still reads it as blank and keeps what is stored.
    assert values["CUBEJS_DB_BQ_KEY_FILE"] == "  "


def test_a_driver_with_no_key_file_is_left_alone():
    values, mounts = split_mounts(get("postgres"), {"CUBEJS_DB_HOST": "h"})
    assert mounts == {}
    assert values == {"CUBEJS_DB_HOST": "h"}


# --- orphans and the shared compose file ------------------------------------


def test_orphans_are_cube_containers_with_no_row():
    containers = {"cube-shop": "running", "cube-gone": "exited", "unrelated": "running"}
    assert docker.orphans(containers, known=["shop"]) == ["cube-gone"]


def test_compose_holds_only_cubestore_and_names_the_network():
    """Sources are not in compose any more. If one appears here, something regressed."""
    doc = yaml.safe_load(pathlib.Path("docker-compose.yml").read_text())
    assert set(doc["services"]) == {"ds-cubestore-router", "ds-cubestore-worker"}
    assert doc["networks"]["analyst-net"]["name"] == docker.NETWORK
    for service in doc["services"].values():
        assert service["networks"] == ["analyst-net"]
