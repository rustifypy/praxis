"""Stage 2 — everything derivable from the schema, with nothing invented.

Two products, both pure functions of what the database said:

  emit_cubes   the structural model: keys, joins, dimensions, `count`
  build_menu   the choices the model layer may pick from

A hallucinated join is a silently wrong number, so nothing here guesses. And a
join path the LLM never saw cannot be hallucinated either, which is what turns
the judgment step into a selection problem.
"""

from dataclasses import dataclass, field

import httpx

from ..registry import Source
from .warehouse import Schema, Table

SYNTHETIC_KEY = "id"
KEY_SEPARATOR = "-"
MAX_PATH_DEPTH = 3
SAMPLE_LIMIT = 12
MAX_SAMPLED_DIMENSIONS = 6
LOW_CARDINALITY = 50

AGGREGATIONS = ("sum", "avg", "min", "max", "count_distinct")


# --- the structural model ---------------------------------------------------


@dataclass
class Cubes:
    bodies: dict[str, dict] = field(default_factory=dict)
    """cube name -> cube body. The one source the enrichment pass builds on."""
    notes: list[str] = field(default_factory=list)


def primary_key_dimension(table: Table) -> dict | None:
    """Cube allows one primary_key dimension, so a composite key is concatenated.

    Kept `public: false` — it satisfies Cube's join requirement without adding a
    meaningless member to what the agent searches.
    """
    if not table.primary_key:
        return None
    if len(table.primary_key) == 1:
        name = table.primary_key[0]
        column = table.column(name)
        return {
            "name": name,
            "sql": name,
            "type": column.type if column else "string",
            "primary_key": True,
            "public": False,
        }
    parts = f", '{KEY_SEPARATOR}', ".join(f"{{CUBE}}.{c}" for c in table.primary_key)
    return {
        "name": SYNTHETIC_KEY,
        "sql": f"CONCAT({parts})",
        "type": "string",
        "primary_key": True,
        "public": False,
    }


def joins(table: Table, schema: Schema, notes: list[str]) -> list[dict]:
    out = []
    for fk in table.foreign_keys:
        target = schema.table(fk.ref_table)
        if target is None:
            notes.append(f"{table.name} -> {fk.ref_table}: table not in this schema")
            continue
        if fk.ref_table == table.name:
            # A cube cannot join to itself under its own name; that needs an alias cube.
            notes.append(f"{table.name} -> {fk.ref_table}: self-referencing join skipped")
            continue
        if not target.primary_key:
            notes.append(f"{table.name} -> {fk.ref_table}: target has no primary key")
            continue
        condition = " AND ".join(
            f"{{CUBE}}.{col} = {{{fk.ref_table}}}.{ref}"
            for col, ref in zip(fk.columns, fk.ref_columns, strict=False)
        )
        out.append({"name": fk.ref_table, "sql": condition, "relationship": "many_to_one"})
    return out


def dimensions(table: Table) -> list[dict]:
    """Every non-key column. Key columns are join plumbing, not slicing dimensions.

    A bare column name, matching what Cube's own generator emits. Cube resolves a
    single-identifier `sql:` against this cube's alias, so joins stay unambiguous.
    """
    return [
        {"name": c.name, "sql": c.name, "type": c.type}
        for c in table.columns
        if c.name not in table.key_columns
    ]


def cube(table: Table, schema: Schema, notes: list[str]) -> dict:
    body: dict = {"name": table.name, "sql_table": table.qualified}

    joined = joins(table, schema, notes)
    if joined:
        body["joins"] = joined

    dims = []
    key = primary_key_dimension(table)
    if key:
        dims.append(key)
    dims.extend(dimensions(table))
    if dims:
        body["dimensions"] = dims

    body["measures"] = [{"name": "count", "type": "count"}]
    return body


def emit_cubes(schema: Schema) -> Cubes:
    """The one place cube bodies are built. Enrichment layers onto these, never re-emits."""
    out = Cubes()
    for table in schema.tables:
        body = cube(table, schema, out.notes)
        if "joins" in body and not any(d.get("primary_key") for d in body.get("dimensions", [])):
            # Cube refuses to compile this combination — drop the joins rather than ship it.
            out.notes.append(f"{table.name}: joins dropped, no primary key available")
            body.pop("joins")
        out.bodies[table.name] = body

    composite = [t.name for t in schema.tables if t.has_composite_key]
    if composite:
        out.notes.append(
            f"synthesised a concatenated key for {', '.join(composite)} — Cube allows one "
            "primary_key dimension"
        )
    out.notes.append("measures: count only — additive measures need the model layer")
    return out


# --- the menu of choices ---------------------------------------------------


@dataclass
class MeasureCandidate:
    cube: str
    column: str
    data_type: str
    distinct: float | None = None
    null_fraction: float | None = None

    @property
    def always_same_value(self) -> bool:
        return self.distinct == 1


@dataclass
class ViewRoot:
    cube: str
    is_fact: bool
    """Nothing references it — the grain where additive facts live."""
    paths: dict[str, list[str]] = field(default_factory=dict)
    """join_path -> members reachable there."""


@dataclass
class Menu:
    measures: list[MeasureCandidate] = field(default_factory=list)
    roots: list[ViewRoot] = field(default_factory=list)
    samples: dict[str, list[str]] = field(default_factory=dict)
    """'cube.dimension' -> a few real values."""

    def for_cube(self, name: str) -> list[MeasureCandidate]:
        return [m for m in self.measures if m.cube == name]


def measure_candidates(schema: Schema) -> list[MeasureCandidate]:
    """Numeric columns that are not keys. Whether they are additive is a judgment."""
    return [
        MeasureCandidate(
            cube=table.name,
            column=column.name,
            data_type=column.data_type,
            distinct=column.distinct,
            null_fraction=column.null_fraction,
        )
        for table in schema.tables
        for column in table.columns
        if column.name not in table.key_columns and column.type == "number"
    ]


def _public_members(table: Table) -> list[str]:
    return [c.name for c in table.columns if c.name not in table.key_columns]


def join_paths(schema: Schema, root: Table, depth: int = MAX_PATH_DEPTH) -> dict[str, list[str]]:
    """Walk many_to_one edges upward from root. Every path is joinable by construction."""
    paths: dict[str, list[str]] = {root.name: _public_members(root)}
    frontier = [(root, root.name, 0)]
    while frontier:
        table, path, level = frontier.pop()
        if level >= depth:
            continue
        for fk in table.foreign_keys:
            if fk.ref_table == table.name:
                continue
            target = schema.table(fk.ref_table)
            if target is None or not target.primary_key:
                continue
            next_path = f"{path}.{target.name}"
            if next_path in paths:
                continue
            paths[next_path] = _public_members(target)
            frontier.append((target, next_path, level + 1))
    return paths


def view_roots(schema: Schema) -> list[ViewRoot]:
    """Fact tables first: nothing points at them, so they cannot fan out."""
    referenced = {
        fk.ref_table for t in schema.tables for fk in t.foreign_keys if fk.ref_table != t.name
    }
    out = [
        ViewRoot(
            cube=table.name,
            is_fact=table.name not in referenced,
            paths=join_paths(schema, table),
        )
        for table in schema.tables
        if any(fk.ref_table != table.name for fk in table.foreign_keys)
    ]
    out.sort(key=lambda r: (not r.is_fact, -len(r.paths)))
    return out


async def build_menu(source: Source, schema: Schema) -> Menu:
    """The candidates, plus real values for the low-cardinality text columns.

    Sampling runs ordinary /v1/load queries against the model that already compiled —
    the same path the agent uses, so nothing new can break here.
    """
    menu = Menu(measures=measure_candidates(schema), roots=view_roots(schema))

    wanted: list[str] = []
    for table in schema.tables:
        picked = 0
        for column in table.columns:
            if picked >= MAX_SAMPLED_DIMENSIONS:
                break
            if column.name in table.key_columns or column.type != "string":
                continue
            if column.distinct is not None and not 0 < column.distinct <= LOW_CARDINALITY:
                continue
            wanted.append(f"{table.name}.{column.name}")
            picked += 1

    async with httpx.AsyncClient(timeout=60) as client:
        for member in wanted:
            query = {"dimensions": [member], "limit": SAMPLE_LIMIT}
            try:
                r = await client.post(f"{source.url}/cubejs-api/v1/load", json={"query": query})
                body = r.json()
            except (httpx.RequestError, ValueError):
                continue
            if "error" in body:
                continue
            values = [str(row.get(member)) for row in body.get("data", []) if row.get(member)]
            if values:
                menu.samples[member] = values[:SAMPLE_LIMIT]
    return menu
