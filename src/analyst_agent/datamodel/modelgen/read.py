"""Stage 1 — read the database, through the connected Cube's own driver.

**A ref, not a container.** The deployment runs wherever its owner put it, so there is nothing
here to start: the probe cubes are committed to a ref of their own, the connected Cube is asked
about that ref, and the ref is deleted. One Cube compiles every ref independently, so this
neither disturbs nor is disturbed by the model being served.

That also removes the last thing that needed dev mode. `/playground/db-schema` is served only
in dev mode, and a deployment on a server is not in dev mode — so tables and columns come from
`information_schema` like everything else, which is portable rather than merely working here.
"""

from dataclasses import dataclass, field

import yaml

from ...core.deployment import Deployment
from ...cube.client import CubeError, open_client
from ...cube.schema import CubeQuery
from ...modelstore import StoreError, open_store
from .warehouse import Column, ForeignKey, Schema, Table

PROBE_REF = "probe"
"""Where the introspection cubes live while they are being queried. Its own ref so the served
model is neither read nor touched, and deleted afterwards — a leftover is harmless but it would
show up in the branch list as something nobody created."""
BOOT_TIMEOUT_S = 90.0
PROBE_QUERY_TIMEOUT_S = 180.0
"""A catalog query scans pg_constraint across every schema; the compile poll has its own."""
ROW_LIMIT = 50_000
"""Cube's own ceiling. A database with more constraints or column statistics than this
is reported as truncated rather than silently half-read."""
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

COLUMN_COLUMNS = ("table_schema", "table_name", "column_name", "data_type")
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


COLUMNS_SQL = f"""
  SELECT table_schema, table_name, column_name, data_type
  FROM information_schema.columns
  WHERE {_not_system("table_schema")}
"""
"""Tables and columns for every driver, not per dialect: `information_schema.columns` is ANSI
and the warehouses here all answer it. This is what `/playground/db-schema` used to give, minus
the requirement that Cube be in dev mode to serve it."""


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


async def read_catalog(deployment: Deployment, timeout: float = BOOT_TIMEOUT_S) -> Introspection:
    """Commit the probe cubes to their own ref, ask the connected Cube about that ref, drop it."""
    if not deployment.credentials:
        raise RuntimeError("no credentials stored — connect a database first")

    catalog = Introspection()

    # Probe cubes wrap a catalog SELECT each; their output columns become string
    # dimensions we can query back as rows.
    dialect = DIALECTS.get(deployment.driver)
    probes: list[tuple[str, str, tuple[str, ...]]] = [
        ("probe_columns", COLUMNS_SQL, COLUMN_COLUMNS)
    ]
    if dialect:
        probes.append(("probe_keys", dialect.keys_sql, KEY_COLUMNS))
        if dialect.stats_sql:
            probes.append(("probe_stats", dialect.stats_sql, STAT_COLUMNS))
    else:
        catalog.notes.append(
            f"no constraint SQL for driver {deployment.driver!r} — cubes will have no joins "
            "and no primary keys"
        )

    cubes = []
    for name, sql, columns in probes:
        body: dict = {"name": name, "sql": sql, "measures": [{"name": "count", "type": "count"}]}
        if columns:
            body["dimensions"] = [{"name": c, "sql": c, "type": "string"} for c in columns]
        cubes.append(body)
    files = {"cubes/probe.yml": yaml.safe_dump({"cubes": cubes}, sort_keys=False)}

    rows: dict[str, list[dict]] = {}
    async with await open_store() as store:
        # Replaced rather than added to: the probe ref holds these cubes and nothing else, so a
        # served model that has stopped compiling cannot stop the database being read.
        try:
            await store.delete_ref(PROBE_REF)
        except StoreError as exc:
            if not exc.missing:
                raise
        await store.create_ref(PROBE_REF)
        await store.replace(PROBE_REF, files, "introspection probe")
        try:
            async with await open_client(ref=PROBE_REF, timeout=PROBE_QUERY_TIMEOUT_S) as cube:
                try:
                    await cube.wait_until_compiled(timeout=timeout)
                except CubeError as exc:
                    raise RuntimeError(f"the probe model did not compile: {exc}") from None

                for name, _, columns in probes:
                    if not columns:
                        continue
                    query = CubeQuery.model_validate(
                        {"dimensions": [f"{name}.{c}" for c in columns], "limit": ROW_LIMIT}
                    )
                    try:
                        loaded = await cube.load(query)
                    except CubeError as exc:
                        # Columns are the catalog itself and keys make joins legal, so losing
                        # either is fatal. Statistics only sharpen the model's judgment.
                        if name != "probe_stats":
                            raise RuntimeError(f"{name}: {str(exc)[:400]}") from None
                        catalog.notes.append(f"statistics unavailable: {str(exc)[:400]}")
                        continue
                    rows[name] = [
                        {c: row.get(f"{name}.{c}") for c in columns} for row in loaded.rows
                    ]
                    if len(rows[name]) >= ROW_LIMIT:
                        catalog.truncated = True
                        catalog.notes.append(
                            f"{name} returned Cube's maximum of {ROW_LIMIT} rows — some keys or "
                            "statistics are missing, so check the joins on a large schema"
                        )
        finally:
            # Disposable from the start, and leaving it would put a ref in the branch list
            # that nobody created.
            await store.delete_ref(PROBE_REF)

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

    # One row per column, so the schema/table shape is rebuilt from them. Order is whatever
    # the warehouse returned, hence the sorts — a picker whose table order moves between reads
    # is worse than one that is merely alphabetical.
    by_table: dict[tuple[str, str], list[dict]] = {}
    for row in rows.get("probe_columns", []):
        schema_name, table_name = row["table_schema"], row["table_name"]
        if schema_name in SYSTEM_SCHEMAS:
            continue
        by_table.setdefault((schema_name, table_name), []).append(row)

    for (schema_name, table_name), columns in sorted(by_table.items()):
        schema = catalog.schemas.setdefault(schema_name, Schema())
        table = Table(name=table_name, schema=schema_name)
        for col in columns:
            stat = statistics.get((schema_name, table_name, col["column_name"]), {})
            table.columns.append(
                Column(
                    name=col["column_name"],
                    data_type=col.get("data_type") or "",
                    distinct=stat.get("distinct_value"),
                    null_fraction=stat.get("null_frac"),
                )
            )
        schema.tables.append(table)

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
