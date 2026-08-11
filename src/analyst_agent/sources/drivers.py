"""Connection shapes per Cube driver."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "cubejs/cube"
IMAGE_ENV = "CUBE_TAG"
LOCALHOST_NOTE = "A database on this Mac is host.docker.internal from inside the container."
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
"""Host values that cannot work from inside a container. The form offers a swap."""


@dataclass(frozen=True)
class ConnField:
    env: str
    prompt: str
    default: str = ""
    secret: bool = False
    required: bool = True
    boolean: bool = False
    fixed: str = ""
    comment: str = ""
    mount_to: str = ""
    """Value is a host path. It gets mounted here, and the env var holds the container path."""


@dataclass(frozen=True)
class Driver:
    key: str
    label: str
    fields: tuple[ConnField, ...]
    image: str = DEFAULT_IMAGE
    tag: str = ""
    """Pinned tag, for a driver that needs a different build. Empty means CUBE_TAG."""
    platform: str = ""


def _host_port_db(port: str) -> tuple[ConnField, ...]:
    return (
        ConnField("CUBEJS_DB_HOST", "Host", "host.docker.internal", comment=LOCALHOST_NOTE),
        ConnField("CUBEJS_DB_PORT", "Port", port),
        ConnField("CUBEJS_DB_NAME", "Database"),
        ConnField("CUBEJS_DB_USER", "User"),
        ConnField("CUBEJS_DB_PASS", "Password", secret=True),
        ConnField("CUBEJS_DB_SSL", "Use SSL", "false", boolean=True, required=False),
    )


DRIVERS: dict[str, Driver] = {
    "postgres": Driver(
        key="postgres",
        label="PostgreSQL",
        fields=_host_port_db("5432"),
    ),
    "mysql": Driver(
        key="mysql",
        label="MySQL",
        fields=_host_port_db("3306"),
    ),
    "snowflake": Driver(
        key="snowflake",
        label="Snowflake",
        fields=(
            ConnField("CUBEJS_DB_SNOWFLAKE_ACCOUNT", "Account (e.g. xy12345.us-east-1)"),
            ConnField("CUBEJS_DB_SNOWFLAKE_WAREHOUSE", "Warehouse"),
            ConnField("CUBEJS_DB_NAME", "Database"),
            ConnField("CUBEJS_DB_USER", "User"),
            ConnField("CUBEJS_DB_PASS", "Password", secret=True),
            ConnField("CUBEJS_DB_SNOWFLAKE_ROLE", "Role", required=False),
        ),
    ),
    "bigquery": Driver(
        key="bigquery",
        label="Google BigQuery",
        fields=(
            ConnField("CUBEJS_DB_BQ_PROJECT_ID", "GCP project id"),
            ConnField(
                "CUBEJS_DB_BQ_KEY_FILE",
                "Path to the service-account key file on this machine",
                mount_to="/cube/conf/bq-key.json",
                comment="Mounted read-only from the host path you entered.",
            ),
        ),
    ),
    "databricks-jdbc": Driver(
        key="databricks-jdbc",
        label="Databricks",
        fields=(
            ConnField("CUBEJS_DB_DATABRICKS_URL", "JDBC URL"),
            ConnField("CUBEJS_DB_DATABRICKS_TOKEN", "Access token", secret=True),
            ConnField(
                "CUBEJS_DB_DATABRICKS_ACCEPT_POLICY",
                "",
                fixed="true",
                comment="Not optional — the JDBC driver refuses to load without it.",
            ),
        ),
        tag="jdk",
        platform="linux/amd64",
    ),
}


def get(name: str) -> Driver:
    try:
        return DRIVERS[name]
    except KeyError:
        raise KeyError(f"unknown driver {name!r} — one of {', '.join(DRIVERS)}") from None


def image_for(spec: Driver) -> str:
    """The image to run. Cube Store publishes no multi-arch `latest`, so CUBE_TAG exists."""
    return f"{spec.image}:{spec.tag or os.environ.get(IMAGE_ENV) or 'latest'}"


def credentials_for(spec: Driver, values: dict[str, str]) -> dict[str, str]:
    """The complete `CUBEJS_DB_*` map for a source, from what the user supplied."""
    out = {"CUBEJS_DB_TYPE": spec.key}
    for field in spec.fields:
        value = values.get(field.env, field.fixed) or field.fixed
        if value != "":
            out[field.env] = value
    return out


def split_mounts(spec: Driver, values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Separate the host paths out of a submitted form: (values, container path -> host path)."""
    kept = dict(values)
    mounts: dict[str, str] = {}
    for field in spec.fields:
        if not field.mount_to:
            continue
        if host_path := (values.get(field.env) or "").strip():
            mounts[field.mount_to] = str(Path(host_path).expanduser())
            kept[field.env] = field.mount_to
    return kept, mounts


def missing_fields(spec: Driver, values: dict[str, str]) -> list[str]:
    """Required connection fields with no value. The form offers them; HTTP has to check."""
    return [
        f.env
        for f in spec.fields
        if f.required and not f.fixed and not values.get(f.env, "").strip()
    ]
