"""The compose file handed over to run a deployment with.

It is the whole interface between this portal and whoever runs the services, and it is
*generated* — so what matters is that it is complete (a missing secret is a deployment that
starts and then refuses every query), self-contained (no `build:`, no `env_file`, nothing
relative to a directory the user does not have), and that no variable survives unresolved.

The templates are inlined in `deployenv` rather than read off disk, so a wheel can render one.
The copies under `services/` are what the images are built from.
"""

import re

import pytest

from analyst_agent.core.deployment import CONTRACT, Deployment
from analyst_agent.datamodel import deployenv

SHOP = Deployment(
    api_secret="secret-" + "s" * 33,
    store_token="write-tok",
    store_read_token="read-tok",
    sandbox_token="sandbox-tok",
    driver="postgres",
    credentials={
        "CUBEJS_DB_TYPE": "postgres",
        "CUBEJS_DB_HOST": "warehouse.internal",
        "CUBEJS_DB_NAME": "shop",
        "CUBEJS_DB_USER": "analyst",
        "CUBEJS_DB_PASS": "hunter2",
    },
)


def settings_in(text: str) -> dict[str, str]:
    """Every service's `environment:` entries as one map — enough for "is it present", which is
    what a missing secret looks like. Keyed on SHOUTY names so compose's own structural keys
    (`image`, `init`, `cpus`) stay out of it."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")) and ": " in line:
            key, _, value = line.partition(": ")
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                out[key] = value.strip()
    return out


def test_no_variable_survives_the_render():
    """The whole point of the guard: a file with `${...}` left in it reads as complete and dies
    at `up` with compose's message about an environment nobody was told to set."""
    for text in (deployenv.semantic(SHOP), deployenv.sandbox(SHOP)):
        assert "$" not in text


def test_the_file_is_self_contained():
    """It is run in whatever directory it was downloaded to, so anything relative to the source
    tree — a build context, a mounted config, an env_file — is a file the user does not have."""
    text = deployenv.semantic(SHOP)
    assert "build:" not in text
    assert "env_file" not in text
    assert "./" not in text, "a relative path cannot resolve where this file is run"


def test_every_secret_the_semantic_layer_needs_is_there():
    """Cube signs with the first and the store checks the other two — one missing is a
    deployment that runs and then refuses everything."""
    env = settings_in(deployenv.semantic(SHOP))
    assert env["CUBEJS_API_SECRET"] == SHOP.api_secret
    assert env["MODEL_STORE_TOKEN"] == "read-tok", "Cube gets the read token"
    assert env["MODEL_STORE_WRITE_TOKEN"] == "write-tok"


def test_the_warehouse_credentials_are_passed_through_whole():
    """Including the password — this is the one place values leave the process, because nothing
    can start the deployment without them."""
    env = settings_in(deployenv.semantic(SHOP))
    assert env["CUBEJS_DB_TYPE"] == "postgres"
    assert env["CUBEJS_DB_PASS"] == "hunter2"
    assert env["CUBEJS_DB_NAME"] == "shop"


def test_whatever_keys_were_configured_are_what_the_file_carries():
    """The marker exists because the key names are the connection's, not this file's: postgres
    has host/user/pass where Databricks has a URL and a token, and a warehouse nothing here has
    heard of has its own. A fixed list in the template could only ever serve one of them."""
    databricks = Deployment(
        driver="databricks-jdbc",
        api_secret="s" * 40,
        store_token="w",
        store_read_token="r",
        credentials={
            "CUBEJS_DB_TYPE": "databricks-jdbc",
            "CUBEJS_DB_DATABRICKS_URL": "jdbc://host",
            "CUBEJS_DB_DATABRICKS_TOKEN": "dapi-tok",
        },
    )
    env = settings_in(deployenv.semantic(databricks))
    assert env["CUBEJS_DB_DATABRICKS_URL"] == "jdbc://host"
    assert "CUBEJS_DB_HOST" not in env, "postgres's keys leaked into a driver without them"


def test_the_sandbox_is_its_own_file_and_the_two_share_nothing():
    """Two deployments, so two files. One holding both would say they must run together, and
    the whole point is that the sandbox can live anywhere, or nowhere."""
    sandbox = settings_in(deployenv.sandbox(SHOP))
    assert sandbox["SANDBOX_TOKEN"] == "sandbox-tok"

    semantic = settings_in(deployenv.semantic(SHOP))
    assert "SANDBOX_TOKEN" not in semantic
    assert not set(sandbox) & set(semantic), "a secret or a port is in both files"


def test_our_images_are_pinned_to_the_contract_and_unqualified():
    """Pinned, because the portal only ever hands over a deployment it speaks the same API as,
    and `latest` is how that stops being true silently.

    Unqualified, because nothing is published: a registry prefix would render a file that fails
    at `up` with a pull error instead of using the image built from this tree."""
    for text in (deployenv.semantic(SHOP), deployenv.sandbox(SHOP)):
        ours = [
            line.split("image:")[1].strip()
            for line in text.splitlines()
            if "image:" in line and "analyst-" in line
        ]
        assert ours
        for image in ours:
            assert image.endswith(f":{CONTRACT}"), image
            assert "/" not in image, f"{image} names a registry and nothing is published there"


def test_production_mode_by_default():
    """Dev mode disables JWT verification entirely, and this file is written to be run on a
    machine that is not this one."""
    assert settings_in(deployenv.semantic(SHOP))["CUBEJS_DEV_MODE"] == "false"


def test_only_the_store_publishes_a_port():
    """Cube is reached through the store at /cube/..., so publishing it would be a second door
    in — and the only one not behind the store's token.

    The *number* is deliberately not asserted against a portal-side constant: the portal is told
    an address and never reaches a guessed one, so which port is published belongs to the compose
    file. Asserted as "exactly one published port, and it is not Cube's"."""
    published = [
        line.strip()
        for line in deployenv.semantic(SHOP).splitlines()
        if line.strip().startswith('- "127.0.0.1:')
    ]
    assert len(published) == 1, published
    assert "4000" not in published[0], "Cube's port is published"
    assert ":8500" in published[0], "the store's container port moved without this test noticing"


def test_the_comments_survive():
    """They are the only documentation for a file the user now owns and is expected to edit,
    which is why this renders textually instead of through pyyaml."""
    text = deployenv.semantic(SHOP)
    assert "# " in text
    assert "not published" in text, "the explanation of why Cube has no port is gone"


def test_a_missing_required_value_is_refused_rather_than_rendered():
    """A deployment whose driver fields were never filled in cannot produce a file. Better a
    named variable than a compose file that fails at `up`."""
    with pytest.raises(RuntimeError) as caught:
        deployenv.semantic(Deployment(driver="postgres"))
    assert "MODEL_STORE_READ_TOKEN" in str(caught.value) or "API_SECRET" in str(caught.value)


def test_a_warehouse_the_portal_does_not_know_renders_the_same():
    """The point of dropping the driver field tables: an unlisted type is configured, not
    refused, and its variables reach the file untouched."""
    env = settings_in(
        deployenv.semantic(
            Deployment(
                driver="clickhouse",
                api_secret="s" * 40,
                store_token="w",
                store_read_token="r",
                credentials={
                    "CUBEJS_DB_TYPE": "clickhouse",
                    "CUBEJS_DB_URL": "https://ch.internal:8443",
                    "CUBEJS_DB_CLICKHOUSE_READONLY": "true",
                },
            )
        )
    )
    assert env["CUBEJS_DB_TYPE"] == "clickhouse"
    assert env["CUBEJS_DB_CLICKHOUSE_READONLY"] == "true"
