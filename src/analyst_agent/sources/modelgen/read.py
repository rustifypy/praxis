"""Stage 1 — read the database, through Cube's own driver."""

import shutil
import socket
from dataclasses import dataclass, field

import httpx
import yaml

from ...core.config import settings
from ...cube.client import CubeClient, CubeError
from ...cube.schema import CubeQuery
from .. import docker
from ..drivers import credentials_for, image_for
from ..registry import Source
from .warehouse import Column, ForeignKey, Schema, Table

PROBE_MODEL_DIR = "probe-model"
CONTAINER = "analyst-probe"
BOOT_TIMEOUT_S = 90.0
PROBE_QUERY_TIMEOUT_S = 180.0
"""A catalog query scans pg_constraint across every schema; the boot poll has its own."""
ROW_LIMIT = 50_000
"""Cube's own ceiling. A database with more constraints or column statistics than this
is reported as truncated rather than silently half-read."""
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

KEY_COLUMNS = (
    "table_schema",
    "table_name",
    "constraint_type",
    "cols",
    "ref_schema",
    "ref_table",
    "ref_cols",
)
STAT_COLUMNS = ("table_schema", "table_name", "column_name", "distinct_value", "null_frac")


@dataclass(frozen=True)
class Dialect:
    keys_sql: str
    """One row per constraint: contype 'p' or 'f', columns comma-joined in key order."""
    stats_sql: str = ""
    """Cardinality and null rate per column, ideally without scanning tables."""


def _not_system(column: str) -> str:
    return f"{column} NOT IN ({', '.join(repr(s) for s in SYSTEM_SCHEMAS)})"


POSTGRES = Dialect(
    keys_sql=f"""
      SELECT
        ns.nspname                                    AS table_schema,
        rel.relname                                   AS table_name,
        con.contype::text                             AS constraint_type,
        (SELECT string_agg(att.attname, ',' ORDER BY u.ord)
           FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
           JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
        )                                             AS cols,
        COALESCE(fns.nspname, '')                     AS ref_schema,
        COALESCE(fref.relname, '')                    AS ref_table,
        COALESCE((SELECT string_agg(att.attname, ',' ORDER BY u.ord)
           FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
           JOIN pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = u.attnum
        ), '')                                        AS ref_cols
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_namespace ns ON ns.oid = rel.relnamespace
      LEFT JOIN pg_class fref ON fref.oid = con.confrelid
      LEFT JOIN pg_namespace fns ON fns.oid = fref.relnamespace
      WHERE con.contype IN ('p', 'f') AND {_not_system("ns.nspname")}
    """,
    # pg_stats is the planner's own sample — no table scan. Rows are missing for
    # anything autovacuum has not analysed yet, so treat absence as "unknown".
    stats_sql=f"""
      SELECT schemaname AS table_schema,
             tablename  AS table_name,
             attname    AS column_name,
             n_distinct AS distinct_value,
             null_frac  AS null_frac
      FROM pg_stats
      WHERE {_not_system("schemaname")}
    """,
)

# A driver with no dialect still gets tables, columns and types from Cube, so its
# cubes are emitted without joins rather than not at all.
DIALECTS: dict[str, Dialect] = {"postgres": POSTGRES}


@dataclass
class Introspection:
    """Everything the connection can see, before anyone chooses what to model."""

    schemas: dict[str, Schema] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    """A catalog query hit Cube's row ceiling — keys or statistics are incomplete."""

    def summary(self) -> list[dict]:
        """One entry per schema, for the picker. Sorted with the biggest first."""
        out = [
            {
                "schema": name,
                "tables": len(schema.tables),
                "keyed": sum(1 for t in schema.tables if t.primary_key),
            }
            for name, schema in self.schemas.items()
        ]
        out.sort(key=lambda s: (-s["tables"], s["schema"]))
        return out


async def read_catalog(source: Source, timeout: float = BOOT_TIMEOUT_S) -> Introspection:
    """Start one throwaway Cube container, read every schema, remove it."""
    if not docker.installed():
        raise RuntimeError("docker not found")
    if not source.credentials:
        raise RuntimeError(f"no credentials stored for {source.name!r}")

    catalog = Introspection()

    # Probe cubes wrap a catalog SELECT each; their output columns become string
    # dimensions we can query back as rows.
    dialect = DIALECTS.get(source.driver)
    probes: list[tuple[str, str, tuple[str, ...]]] = []
    if dialect:
        probes.append(("probe_keys", dialect.keys_sql, KEY_COLUMNS))
        if dialect.stats_sql:
            probes.append(("probe_stats", dialect.stats_sql, STAT_COLUMNS))
    else:
        catalog.notes.append(
            f"no constraint SQL for driver {source.driver!r} — cubes will have no joins "
            "and no primary keys"
        )
        # Cube will not serve /v1/meta at all unless the model has at least one cube.
        probes.append(("probe_noop", "SELECT 1 AS one", ()))

    probe_dir = settings.dir_path / PROBE_MODEL_DIR
    shutil.rmtree(probe_dir, ignore_errors=True)
    (probe_dir / "cubes").mkdir(parents=True)
    cubes = []
    for name, sql, columns in probes:
        body: dict = {"name": name, "sql": sql, "measures": [{"name": "count", "type": "count"}]}
        if columns:
            body["dimensions"] = [{"name": c, "sql": c, "type": "string"} for c in columns]
        cubes.append(body)
    (probe_dir / "cubes" / "probe.yml").write_text(
        yaml.safe_dump({"cubes": cubes}, sort_keys=False)
    )

    # Port 0 lets the OS pick, so two runs cannot collide. Bound to loopback because
    # dev mode — which is what serves /playground/db-schema — disables JWT
    # verification entirely; this container answers anyone who can reach it.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    spec = source.spec
    environment: list[str] = []
    for key, value in credentials_for(spec, source.credentials).items():
        environment += ["-e", f"{key}={value}"]

    await docker.remove(CONTAINER)
    # fmt: off
    started = await docker.run(
        "run", "-d",
        "--name", CONTAINER,
        *environment,
        "-e", "CUBEJS_DEV_MODE=true",
        "-e", "CUBEJS_API_SECRET=probe",
        "-p", f"127.0.0.1:{port}:4000",
        "-v", f"{probe_dir}:{docker.MODEL_MOUNT}",
        *(("--platform", spec.platform) if spec.platform else ()),
        image_for(spec),
        timeout=180,
    )
    # fmt: on
    if not started.ok:
        shutil.rmtree(probe_dir, ignore_errors=True)
        raise RuntimeError(f"could not start probe container: {started.message}")

    api = f"http://localhost:{port}"
    try:
        # `secret=None` is the only unauthenticated session there is, and this is what it is
        # for: the probe's container is started with a 5-byte secret nothing can sign with.
        async with CubeClient(
            f"{api}{settings.cube_base_path}", None, timeout=PROBE_QUERY_TIMEOUT_S
        ) as cube:
            try:
                await cube.wait_until_compiled(timeout=timeout)
            except CubeError as exc:
                raise RuntimeError(f"probe container did not serve /v1/meta: {exc}") from None

            # Tables, columns and types — all Cube's own introspection exposes. It
            # returns every schema keyed by name, which is why this reads them all. Raw httpx:
            # `/playground` is outside the API's base path and dev mode alone serves it.
            async with httpx.AsyncClient(timeout=PROBE_QUERY_TIMEOUT_S) as raw:
                r = await raw.get(f"{api}/playground/db-schema")
            if r.status_code != 200:
                raise RuntimeError(
                    f"/playground/db-schema returned {r.status_code} — is dev mode enabled?"
                )
            tables_by_schema = r.json().get("tablesSchema") or {}

            # Query each probe cube back as rows. This is where the catalog SQL runs.
            rows: dict[str, list[dict]] = {}
            for name, _, columns in probes:
                if not columns:
                    continue
                query = CubeQuery.model_validate(
                    {"dimensions": [f"{name}.{c}" for c in columns], "limit": ROW_LIMIT}
                )
                try:
                    loaded = await cube.load(query)
                except CubeError as exc:
                    # Keys make joins legal, so losing them is fatal. Statistics only
                    # sharpen the model's judgment, so losing them is a note.
                    if name == "probe_keys":
                        raise RuntimeError(f"{name}: {str(exc)[:400]}") from None
                    catalog.notes.append(f"statistics unavailable: {str(exc)[:400]}")
                    continue
                rows[name] = [{c: row.get(f"{name}.{c}") for c in columns} for row in loaded.rows]
                if len(rows[name]) >= ROW_LIMIT:
                    catalog.truncated = True
                    catalog.notes.append(
                        f"{name} returned Cube's maximum of {ROW_LIMIT} rows — some keys or "
                        "statistics are missing, so check the joins on a large schema"
                    )
    finally:
        # The container was disposable from the start.
        await docker.remove(CONTAINER)
        shutil.rmtree(probe_dir, ignore_errors=True)

    # Statistics arrive as strings, because every probe dimension is declared as a
    # string, so convert them here — once — and leave anything unparsable as None.
    statistics: dict[tuple[str, str, str], dict] = {}
    for row in rows.get("probe_stats", []):
        numbers = {}
        for key in ("distinct_value", "null_frac"):
            try:
                numbers[key] = float(row[key])
            except (KeyError, TypeError, ValueError):
                numbers[key] = None
        statistics[(row["table_schema"], row["table_name"], row["column_name"])] = numbers

    for schema_name, tables in sorted(tables_by_schema.items()):
        if schema_name in SYSTEM_SCHEMAS:
            continue
        schema = Schema()
        for table_name, columns in sorted(tables.items()):
            table = Table(name=table_name, schema=schema_name)
            for col in columns:
                stat = statistics.get((schema_name, table_name, col["name"]), {})
                table.columns.append(
                    Column(
                        name=col["name"],
                        data_type=col.get("type", ""),
                        distinct=stat.get("distinct_value"),
                        null_fraction=stat.get("null_frac"),
                    )
                )
            schema.tables.append(table)
        catalog.schemas[schema_name] = schema

    for row in rows.get("probe_keys", []):
        schema = catalog.schemas.get(row["table_schema"])
        table = schema.table(row["table_name"]) if schema else None
        if table is None:
            continue
        key_columns = [c for c in (row.get("cols") or "").split(",") if c]
        if row["constraint_type"] == "p":
            table.primary_key = key_columns
        elif row["constraint_type"] == "f":
            # A cube references a joined cube by bare name, so a foreign key pointing
            # into another schema is not expressible — record it only when both ends
            # live together.
            if (row.get("ref_schema") or row["table_schema"]) != row["table_schema"]:
                continue
            table.foreign_keys.append(
                ForeignKey(
                    columns=key_columns,
                    ref_table=row["ref_table"],
                    ref_columns=[c for c in (row.get("ref_cols") or "").split(",") if c],
                )
            )

    return catalog
