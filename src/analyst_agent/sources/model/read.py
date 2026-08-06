"""Stage 1 — read the database, through Cube's own driver.

Cube reports columns but not constraints, and it has no arbitrary-SQL endpoint —
but a cube's `sql:` may be any SELECT. So one throwaway Cube container, holding
nothing but probe cubes, answers all three questions:

    GET  /playground/db-schema   tables, columns, types   (needs dev mode)
    POST /v1/load                primary and foreign keys (pg_constraint)
    POST /v1/load                cardinality, null rates  (pg_stats)

No second database driver, no credentials of our own, and the live source
container is never touched.
"""

import asyncio
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass

import httpx
import yaml

from ..registry import SCRATCH_DIR, Source, project_root
from .warehouse import Column, ForeignKey, Schema, Table

PROBE_MODEL_DIR = "probe-model"
CONTAINER = "analyst-probe"
IMAGE_ENV = "CUBE_TAG"
BOOT_TIMEOUT_S = 90.0
ROW_LIMIT = 5000

KEY_COLUMNS = ("table_name", "constraint_type", "cols", "ref_table", "ref_cols")
STAT_COLUMNS = ("table_name", "column_name", "distinct_value", "null_frac")


@dataclass(frozen=True)
class Dialect:
    keys_sql: str
    """One row per constraint: contype 'p' or 'f', columns comma-joined in key order."""
    stats_sql: str = ""
    """Cardinality and null rate per column, ideally without scanning tables."""


POSTGRES = Dialect(
    keys_sql="""
      SELECT
        rel.relname                                   AS table_name,
        con.contype::text                             AS constraint_type,
        (SELECT string_agg(att.attname, ',' ORDER BY u.ord)
           FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
           JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
        )                                             AS cols,
        COALESCE(fref.relname, '')                    AS ref_table,
        COALESCE((SELECT string_agg(att.attname, ',' ORDER BY u.ord)
           FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
           JOIN pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = u.attnum
        ), '')                                        AS ref_cols
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_namespace ns ON ns.oid = rel.relnamespace
      LEFT JOIN pg_class fref ON fref.oid = con.confrelid
      WHERE con.contype IN ('p', 'f') AND ns.nspname = '{schema}'
    """,
    # pg_stats is the planner's own sample — no table scan. Rows are missing for
    # anything autovacuum has not analysed yet, so treat absence as "unknown".
    stats_sql="""
      SELECT tablename AS table_name,
             attname   AS column_name,
             n_distinct AS distinct_value,
             null_frac  AS null_frac
      FROM pg_stats
      WHERE schemaname = '{schema}'
    """,
)

# A driver with no dialect still gets tables, columns and types from Cube, so its
# cubes are emitted without joins rather than not at all.
DIALECTS: dict[str, Dialect] = {"postgres": POSTGRES}


async def introspect(source: Source, schema: str = "public") -> tuple[Schema, list[str]]:
    """Start one throwaway Cube container, read the catalog, remove it.

    Nine steps, top to bottom. Returns the schema, plus any notes worth showing
    the user.
    """

    # 1. Nothing here works without docker and the source's credentials.
    if not shutil.which("docker"):
        raise RuntimeError("docker not found")
    root = project_root()
    env_file = source.env_file(root)
    if not env_file.exists():
        raise RuntimeError(f"no credentials at {env_file}")

    notes: list[str] = []

    # 2. Decide which probe cubes to write. Each one wraps a catalog SELECT, and its
    #    output columns become string dimensions we can query back as rows.
    dialect = DIALECTS.get(source.driver)
    probes: list[tuple[str, str, tuple[str, ...]]] = []
    if dialect:
        probes.append(("probe_keys", dialect.keys_sql.format(schema=schema), KEY_COLUMNS))
        if dialect.stats_sql:
            probes.append(("probe_stats", dialect.stats_sql.format(schema=schema), STAT_COLUMNS))
    else:
        notes.append(
            f"no constraint SQL for driver {source.driver!r} — cubes will have no joins "
            "and no primary keys"
        )
        # Cube will not serve /v1/meta at all unless the model has at least one cube.
        probes.append(("probe_noop", "SELECT 1 AS one", ()))

    # 3. Write a model directory holding those cubes and nothing else, so no unrelated
    #    file can fail to compile and take the whole read down with it.
    probe_dir = root / SCRATCH_DIR / PROBE_MODEL_DIR
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

    # 4. Start the container. Port 0 lets the OS pick, so two runs cannot collide, and
    #    dev mode is what exposes /playground/db-schema.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, text=True)
    # fmt: off
    started = subprocess.run(
        [
            "docker", "run", "-d",
            "--name", CONTAINER,
            "--env-file", str(env_file),
            "-e", "CUBEJS_DEV_MODE=true",
            "-e", "CUBEJS_API_SECRET=probe",
            "-p", f"{port}:4000",
            "-v", f"{probe_dir}:/cube/conf/model",
            f"cubejs/cube:{os.environ.get(IMAGE_ENV) or 'latest'}",
        ],
        cwd=root, capture_output=True, text=True,
    )
    # fmt: on
    if started.returncode != 0:
        shutil.rmtree(probe_dir, ignore_errors=True)
        raise RuntimeError(f"could not start probe container: {started.stderr.strip()[:400]}")

    api = f"http://localhost:{port}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # 5. Wait for Cube to compile and serve the probe model.
            deadline = asyncio.get_running_loop().time() + BOOT_TIMEOUT_S
            last = "no response"
            while asyncio.get_running_loop().time() < deadline:
                try:
                    r = await client.get(f"{api}/cubejs-api/v1/meta")
                    if r.status_code == 200 and "cubes" in r.text:
                        break
                    last = r.text[:300]
                except httpx.RequestError as exc:
                    last = type(exc).__name__
                await asyncio.sleep(2)
            else:
                raise RuntimeError(
                    f"probe container did not serve /v1/meta within {BOOT_TIMEOUT_S:.0f}s — {last}"
                )

            # 6. Tables, columns and types — all Cube's own introspection exposes.
            r = await client.get(f"{api}/playground/db-schema", timeout=120)
            if r.status_code != 200:
                raise RuntimeError(
                    f"/playground/db-schema returned {r.status_code} — is dev mode enabled?"
                )
            tables = (r.json().get("tablesSchema") or {}).get(schema, {})

            # 7. Query each probe cube back as rows. This is where the catalog SQL runs.
            rows: dict[str, list[dict]] = {}
            for name, _, columns in probes:
                if not columns:
                    continue
                query = {"dimensions": [f"{name}.{c}" for c in columns], "limit": ROW_LIMIT}
                answer = await client.post(
                    f"{api}/cubejs-api/v1/load", json={"query": query}, timeout=120
                )
                body = answer.json()
                if "error" in body:
                    # Keys make joins legal, so losing them is fatal. Statistics only
                    # sharpen the model's judgment, so losing them is a note.
                    if name == "probe_keys":
                        raise RuntimeError(f"{name}: {str(body['error'])[:400]}")
                    notes.append(f"statistics unavailable: {str(body['error'])[:400]}")
                    continue
                rows[name] = [
                    {c: row.get(f"{name}.{c}") for c in columns} for row in body.get("data", [])
                ]

        # 8. Merge the reads into one Schema. Statistics arrive as strings, because every
        #    probe dimension is declared as a string, so convert them here — once — and
        #    leave anything missing or unparsable as None.
        statistics: dict[tuple[str, str], dict] = {}
        for row in rows.get("probe_stats", []):
            numbers = {}
            for key in ("distinct_value", "null_frac"):
                try:
                    numbers[key] = float(row[key])
                except (KeyError, TypeError, ValueError):
                    numbers[key] = None
            statistics[(row["table_name"], row["column_name"])] = numbers

        out = Schema()
        for table_name, columns in sorted(tables.items()):
            table = Table(name=table_name, schema=schema)
            for col in columns:
                stat = statistics.get((table_name, col["name"]), {})
                table.columns.append(
                    Column(
                        name=col["name"],
                        data_type=col.get("type", ""),
                        distinct=stat.get("distinct_value"),
                        null_fraction=stat.get("null_frac"),
                    )
                )
            out.tables.append(table)

        for row in rows.get("probe_keys", []):
            table = out.table(row["table_name"])
            if table is None:
                continue
            key_columns = [c for c in (row.get("cols") or "").split(",") if c]
            if row["constraint_type"] == "p":
                table.primary_key = key_columns
            elif row["constraint_type"] == "f":
                table.foreign_keys.append(
                    ForeignKey(
                        columns=key_columns,
                        ref_table=row["ref_table"],
                        ref_columns=[c for c in (row.get("ref_cols") or "").split(",") if c],
                    )
                )

        # 9. Say so when a table has no key: Cube rejects a joined cube without one, so
        #    those joins get dropped later and the user should know why.
        keyless = sorted(t.name for t in out.tables if not t.primary_key)
        if keyless and rows.get("probe_keys"):
            notes.append(
                f"no primary key on {', '.join(keyless)} — joins to these are omitted, "
                "since Cube rejects a joined cube without one"
            )
        return out, notes
    finally:
        # The container was disposable from the start.
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, text=True)
        shutil.rmtree(probe_dir, ignore_errors=True)
