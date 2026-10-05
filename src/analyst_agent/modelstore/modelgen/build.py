"""Stage 2 — everything derivable from the schema, with nothing invented."""

from dataclasses import dataclass, field

from ..client import StoreError, open_store
from ..schema import CubeQuery
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


def primary_key_dimension(table: Table, public: bool = False) -> dict | None:
    """Cube allows one primary_key dimension, so a composite key is concatenated.

    `public` only for a single-column key: a concatenation is never something to slice by.
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
            "public": public,
        }
    parts = f", '{KEY_SEPARATOR}', ".join(f"{{CUBE}}.{column}" for column in table.primary_key)
    return {
        "name": SYNTHETIC_KEY,
        "sql": f"CONCAT({parts})",
        "type": "string",
        "primary_key": True,
        "public": False,
    }


def joins(table: Table, schema: Schema, notes: list[str]) -> list[dict]:
    out = []
    for foreign_key in table.foreign_keys:
        target = schema.table(foreign_key.ref_table)
        if foreign_key.ref_table in table.ambiguous_targets:
            columns = ", ".join(foreign_key.columns)
            notes.append(
                f"{table.name} -> {foreign_key.ref_table}: more than one key reaches it, so no "
                f"join — {columns} is kept as a dimension"
            )
            continue
        if target is None:
            notes.append(f"{table.name} -> {foreign_key.ref_table}: table not in this schema")
            continue
        if foreign_key.ref_table == table.name:
            # A cube cannot join to itself under its own name; that needs an alias cube.
            notes.append(f"{table.name} -> {foreign_key.ref_table}: self-referencing join skipped")
            continue
        if not target.primary_key:
            notes.append(f"{table.name} -> {foreign_key.ref_table}: target has no primary key")
            continue
        condition = " AND ".join(
            f"{{CUBE}}.{col} = {{{foreign_key.ref_table}}}.{ref}"
            for col, ref in zip(foreign_key.columns, foreign_key.ref_columns, strict=False)
        )
        out.append({"name": foreign_key.ref_table, "sql": condition, "relationship": "many_to_one"})
    return out


def dimensions(table: Table) -> list[dict]:
    """Every non-key column. Key columns are join plumbing, not slicing dimensions."""
    return [
        {"name": column.name, "sql": column.name, "type": column.type}
        for column in table.columns
        if column.name not in table.key_columns
    ]


def cube(table: Table, schema: Schema, notes: list[str]) -> dict:
    body: dict = {"name": table.name, "sql_table": table.qualified}

    joined = joins(table, schema, notes)
    if joined:
        body["joins"] = joined

    dims = []
    key = primary_key_dimension(table, public=table.name in schema.exposed_keys)
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
        if "joins" in body and not any(
            dimension.get("primary_key") for dimension in body.get("dimensions", [])
        ):
            # Cube refuses to compile this combination — drop the joins rather than ship it.
            out.notes.append(f"{table.name}: joins dropped, no primary key available")
            body.pop("joins")
        out.bodies[table.name] = body

    composite = [table.name for table in schema.tables if table.has_composite_key]
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
        return [measure for measure in self.measures if measure.cube == name]


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
        # Every part of the key, not only `key_columns`: a composite key's date is a dimension
        # now, and its ids would otherwise be offered for summing.
        if column.name not in table.key_columns | set(table.primary_key) and column.type == "number"
    ]


def _public_members(table: Table, schema: Schema) -> list[str]:
    shown = [column.name for column in table.columns if column.name not in table.key_columns]
    if table.name in schema.exposed_keys and len(table.primary_key) == 1:
        shown.insert(0, table.primary_key[0])
    return shown


def join_paths(schema: Schema, root: Table, depth: int = MAX_PATH_DEPTH) -> dict[str, list[str]]:
    """Walk many_to_one edges upward from root. Every path is joinable by construction."""
    paths: dict[str, list[str]] = {root.name: _public_members(root, schema)}
    frontier = [(root, root.name, 0)]
    while frontier:
        table, path, level = frontier.pop()
        if level >= depth:
            continue
        for foreign_key in table.joined_keys:
            if foreign_key.ref_table == table.name:
                continue
            target = schema.table(foreign_key.ref_table)
            if target is None or not target.primary_key:
                continue
            next_path = f"{path}.{target.name}"
            if next_path in paths:
                continue
            paths[next_path] = _public_members(target, schema)
            frontier.append((target, next_path, level + 1))
    return paths


def view_roots(schema: Schema) -> list[ViewRoot]:
    """Fact tables first: nothing points at them, so they cannot fan out."""
    referenced = {
        foreign_key.ref_table
        for table in schema.tables
        for foreign_key in table.foreign_keys
        if foreign_key.ref_table != table.name
    }
    out = [
        ViewRoot(
            cube=table.name,
            is_fact=table.name not in referenced,
            paths=join_paths(schema, table),
        )
        for table in schema.tables
        if any(foreign_key.ref_table != table.name for foreign_key in table.foreign_keys)
    ]
    out.sort(key=lambda r: (not r.is_fact, -len(r.paths)))
    return out


async def build_menu(schema: Schema) -> Menu:
    """The candidates, plus real values for the low-cardinality text columns."""
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

    async with await open_store() as store:
        cube = store.at()
        for member in wanted:
            query = CubeQuery.model_validate({"dimensions": [member], "limit": SAMPLE_LIMIT})
            try:
                loaded = await cube.load(query)
            except StoreError:
                # A sample only sharpens the model's judgment; a column that will not
                # aggregate is not a reason to fail the build.
                continue
            values = [str(row.get(member)) for row in loaded.rows if row.get(member)]
            if values:
                menu.samples[member] = values[:SAMPLE_LIMIT]
    return menu
