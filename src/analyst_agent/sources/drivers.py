"""Connection shapes per Cube driver.

Field names are Cube's own `CUBEJS_DB_*` variables — this module adds no
indirection of its own, so anything Cube documents works here verbatim.
"""

from dataclasses import dataclass

DEFAULT_IMAGE = "cubejs/cube:${CUBE_TAG:-latest}"
LOCALHOST_NOTE = "A database on this Mac is host.docker.internal from inside the container."


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
        image="cubejs/cube:jdk",
        platform="linux/amd64",
    ),
}


def get(name: str) -> Driver:
    try:
        return DRIVERS[name]
    except KeyError:
        raise KeyError(f"unknown driver {name!r} — one of {', '.join(DRIVERS)}") from None
