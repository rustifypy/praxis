"""The compose file handed over to run a deployment with.

It is the whole interface between this portal and whoever runs the services, and it is
*generated* — so what matters is that it is complete (a missing secret is a deployment that
starts and then refuses every query), self-contained (no `build:`, no `env_file`, nothing
relative to a directory the user does not have), and that no variable survives unresolved.

The templates are inlined in `deployenv` rather than read off disk, so a wheel can render one.
The semantic layer is published images built elsewhere; only the sandbox is still built here.
"""

import json
import platform
import re

import pytest
import yaml

from praxis.core import deployenv
from praxis.core.deployment import Deployment

SHOP = Deployment(
    api_secret="secret-" + "s" * 33,
    store_token="write-tok",
    sandbox_token="sandbox-tok",
    driver="postgres",
    credentials={
        "CUBEJS_DB_TYPE": "postgres",
        "CUBEJS_DB_HOST": "warehouse.internal",
        "CUBEJS_DB_NAME": "shop",
        "CUBEJS_DB_USER": "praxis",
        "CUBEJS_DB_PASS": "hunter2",
    },
)


def settings_in(text: str) -> dict[str, str]:
    """Every service's `environment:` entries as one map — enough for "is it present", which is
    what a missing secret looks like. Keyed on SHOUTY names so compose's own structural keys
    (`image`, `init`, `cpus`) stay out of it.

    Credential values arrive quoted, so this reads them the way YAML would: a certificate is
    several lines and a key file is JSON, and neither is a bare scalar."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")) and ": " in line:
            key, _, value = line.partition(": ")
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                value = value.strip()
                out[key] = json.loads(value) if value.startswith('"') else value
    return out


def test_no_variable_survives_the_render():
    """The whole point of the guard: a file with `${...}` left in it reads as complete and dies
    at `up` with compose's message about an environment nobody was told to set.

    A literal `$` may appear, but only doubled — compose interpolates the whole file after
    parsing it, config content included, so a single one is a variable nobody set."""
    for text in (deployenv.semantic(SHOP), deployenv.sandbox(SHOP)):
        assert "${" not in text
        assert "$" not in text.replace("$$", "")


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
    assert env["MODELSTORE_SERVICE_TOKEN"] == "write-tok"


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


def test_no_image_tracks_a_moving_tag():
    """The store reports no contract, so which build a hand-over names is the only compatibility
    control there is — and `latest` means "whatever was newest on the machine that pulled it",
    which is how a file that worked yesterday starts refusing every query."""
    text = deployenv.semantic(SHOP)
    images = [line.split("image:")[1].strip() for line in text.splitlines() if "image:" in line]
    assert len(images) == 3
    for image in images:
        tag = image.split("/")[-1].partition(":")[2]
        assert tag, f"{image} is untagged"
        assert tag != "latest", f"{image} tracks a moving tag"


def test_the_cubestore_image_can_be_pulled_where_the_file_was_generated():
    """Cube Store publishes one architecture per tag and no multi-arch manifest, so the wrong
    one fails at `up` with "no matching manifest for linux/arm64/v8" rather than falling back.
    A hand-over naming `latest` on an Apple Silicon machine cannot start at all, and hand-editing
    a generated file is not something anyone should have to know to do."""
    image = next(
        line.split("image:")[1].strip()
        for line in deployenv.semantic(SHOP).splitlines()
        if "image:" in line and "cubestore" in line
    )
    arm = platform.machine() in ("arm64", "aarch64")
    assert image == f"cubejs/cubestore:{deployenv.CUBE_VERSION}{'-arm64v8' if arm else ''}"


def test_production_mode_by_default():
    """Dev mode disables JWT verification entirely, and this file is written to be run on a
    machine that is not this one."""
    assert settings_in(deployenv.semantic(SHOP))["CUBEJS_DEV_MODE"] == "false"


def test_only_the_store_publishes_a_port():
    """Cube is reached through the store at /refs/<ref>/query, so publishing it would be a
    second door in — and the only one not behind the store's token.

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
    assert ":8000" in published[0], "the store's container port moved without this test noticing"


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
    assert "MODELSTORE_SERVICE_TOKEN" in str(caught.value) or "API_SECRET" in str(caught.value)


def test_a_pasted_key_and_a_pasted_certificate_survive_the_file():
    """Both are text someone pastes, because the deployment reading them may not be on this
    machine and a file path would name a file it cannot see. Neither is a YAML scalar: a key
    file starts with `{`, which bare would parse as a flow mapping, and a certificate is several
    lines, which bare is not a value at all. So this asserts through a real YAML parse."""
    certificate = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    text = deployenv.semantic(
        Deployment(
            driver="bigquery",
            api_secret="s" * 40,
            store_token="w",
            credentials={
                "CUBEJS_DB_TYPE": "bigquery",
                "CUBEJS_DB_BQ_CREDENTIALS": '{"type": "service_account", "project_id": "acme"}',
                "CUBEJS_DB_SSL_CA": certificate,
            },
        )
    )
    env = yaml.safe_load(text)["services"]["cube-api"]["environment"]
    assert json.loads(env["CUBEJS_DB_BQ_CREDENTIALS"])["project_id"] == "acme"
    assert env["CUBEJS_DB_SSL_CA"] == certificate, "the certificate lost its line breaks"


def test_a_dollar_sign_in_a_password_reaches_the_deployment_whole():
    """Compose interpolates its own variables *after* parsing the YAML, so `$` doubles on the
    way out: unescaped, `pa$sword` would arrive as `pa` plus a warning about an environment
    nobody was asked to set."""
    text = deployenv.semantic(
        Deployment(
            driver="postgres",
            api_secret="s" * 40,
            store_token="w",
            credentials={"CUBEJS_DB_PASS": "pa$sword"},
        )
    )
    written = yaml.safe_load(text)["services"]["cube-api"]["environment"]["CUBEJS_DB_PASS"]
    assert written == "pa$$sword", "compose will eat the rest of this value"
    assert written.replace("$$", "$") == "pa$sword", "what the container ends up with"


def test_what_a_warehouse_cannot_connect_without_is_answered_in_words():
    """The answer becomes a message someone reads while a form is still on screen, so it is the
    words from the form and never the spelling of an environment variable."""
    assert deployenv.missing("postgres", {"CUBEJS_DB_HOST": "db"}) == ["a database", "a user"]
    assert deployenv.missing("postgres", {"CUBEJS_DB_HOST": "  "}) == [
        "a host",
        "a database",
        "a user",
    ], "blank is missing, not present"
    assert deployenv.missing("bigquery", {}) == ["a project", "a service account key"]


def test_a_warehouse_this_has_never_heard_of_is_owed_nothing():
    """The counterpart of the free-form settings in the form: an unlisted warehouse is connected
    by typing its own, and a guessed requirement would refuse exactly that case."""
    assert deployenv.missing("timescale", {}) == []


def test_a_warehouse_the_portal_does_not_know_renders_the_same():
    """The point of dropping the driver field tables: an unlisted type is configured, not
    refused, and its variables reach the file untouched."""
    env = settings_in(
        deployenv.semantic(
            Deployment(
                driver="clickhouse",
                api_secret="s" * 40,
                store_token="w",
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
