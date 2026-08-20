"""The environment file handed over to run the deployment with.

It is the whole interface between this portal and whoever runs Cube, so what matters is that
nothing the compose file needs is missing — a missing secret is a deployment that starts and
then refuses every query, diagnosed hours later.
"""

from analyst_agent.core.deployment import Deployment
from analyst_agent.datamodel.deployenv import render, render_sandbox
from analyst_agent.datamodel.drivers import DRIVERS, required_envs

SHOP = Deployment(
    api_secret="secret-" + "s" * 33,
    store_token="write-tok",
    store_read_token="read-tok",
    sandbox_token="sandbox-tok",
    driver="postgres",
    credentials={
        "CUBEJS_DB_HOST": "warehouse.internal",
        "CUBEJS_DB_NAME": "shop",
        "CUBEJS_DB_USER": "analyst",
        "CUBEJS_DB_PASS": "hunter2",
    },
)


def settings_in(text: str) -> dict[str, str]:
    """The file as docker compose would read it — comments and blanks dropped."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key] = value
    return out


def test_every_secret_the_semantic_layer_needs_is_there():
    """The three the portal minted for it. Cube signs with the first, and the store checks the
    other two — one missing is a deployment that runs and then refuses everything."""
    env = settings_in(render(SHOP))
    assert env["CUBEJS_API_SECRET"] == SHOP.api_secret
    assert env["MODEL_STORE_READ_TOKEN"] == "read-tok"
    assert env["MODEL_STORE_WRITE_TOKEN"] == "write-tok"


def test_the_sandbox_gets_its_own_file_and_the_two_share_nothing():
    """Two deployments, so two files. One file holding both would say they have to run
    together, and the whole point is that the sandbox can live anywhere or nowhere."""
    sandbox = settings_in(render_sandbox(SHOP))
    assert sandbox == {"SANDBOX_TOKEN": "sandbox-tok", "SANDBOX_PORT": "8600"}

    semantic = settings_in(render(SHOP))
    assert "SANDBOX_TOKEN" not in semantic
    assert not set(sandbox) & set(semantic), "no secret and no port is in both files"


def test_the_warehouse_credentials_are_passed_through_whole():
    """Including the password — this is the one place values leave the process, because nothing
    can run Cube without them."""
    env = settings_in(render(SHOP))
    assert env["CUBEJS_DB_TYPE"] == "postgres"
    assert env["CUBEJS_DB_PASS"] == "hunter2"
    assert env["CUBEJS_DB_NAME"] == "shop"


def test_production_mode_by_default():
    """Dev mode disables JWT verification entirely, and this file is written to be run on a
    machine that is not this one."""
    assert settings_in(render(SHOP))["CUBEJS_DEV_MODE"] == "false"


def test_only_the_store_publishes_a_port():
    """Cube is reached through the store at /cube/..., so publishing it would be a second door
    into the deployment — and the only one not behind the store's token."""
    env = settings_in(render(SHOP))
    assert env["MODEL_STORE_PORT"] == "8500"
    assert "CUBE_PORT" not in env


def test_a_driver_needing_its_own_cube_build_says_so():
    """`CUBE_TAG` is how the compose file picks the image, so a driver whose spec pins one has
    to put it in the file rather than leave a driver-not-found at query time."""
    pinned = [key for key, spec in DRIVERS.items() if spec.tag]
    assert pinned, "the point of this test is a driver with a pinned tag"
    env = settings_in(render(Deployment(driver=pinned[0])))
    assert env["CUBE_TAG"] == DRIVERS[pinned[0]].tag


def test_a_key_file_is_named_rather_than_silently_dropped():
    """The compose file mounts nothing extra, so a driver needing a key file is the one thing
    this cannot hand over. Both paths are named so it is a task rather than a mystery."""
    text = render(
        Deployment(driver="postgres", mounts={"/cube/conf/key.json": "/Users/me/key.json"})
    )
    assert "/Users/me/key.json" in text and "/cube/conf/key.json" in text


def test_every_required_field_of_every_driver_survives_rendering():
    """The end-to-end pin: a field the form insists on and the file omits is a deployment that
    cannot connect, and the failure surfaces far from here."""
    for key, spec in DRIVERS.items():
        values = {env: f"value-for-{env}" for env in required_envs(spec)}
        deployment = Deployment(driver=key, credentials={"CUBEJS_DB_TYPE": key, **values})
        env = settings_in(render(deployment))
        missing = [name for name in values if name not in env]
        assert not missing, f"{key}: {missing}"
