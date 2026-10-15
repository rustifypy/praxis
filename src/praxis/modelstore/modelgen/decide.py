"""Stage 3 — the judgment the catalog cannot supply, and the guards on it."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from pydantic import BaseModel, Field

from ...core.llm import build_model
from .build import AGGREGATIONS, Menu, ViewRoot
from .warehouse import Schema, Table

# --- what the model is allowed to say --------------------------------------


class MemberDescription(BaseModel):
    name: str = Field(description="Existing member name. Never invent or rename.")
    description: str = Field(description="One sentence, plain business language.")
    ai_context: str = Field(
        default="", description="Only when genuinely ambiguous: which measure to prefer, caveats."
    )


class MeasureDecision(BaseModel):
    column: str = Field(description="A column from the candidate list.")
    name: str = Field(description="snake_case measure name, e.g. revenue.")
    aggregation: str = Field(description=f"One of: {', '.join(AGGREGATIONS)}.")
    expression: str = Field(
        default="",
        description="Optional SQL over columns of THIS table only, e.g. unit_price * quantity. "
        "Leave empty to aggregate the column directly.",
    )
    description: str = ""
    ai_context: str = ""


class CubeDecision(BaseModel):
    description: str = Field(description="What one row of this table represents.")
    members: list[MemberDescription] = Field(default_factory=list)
    measures: list[MeasureDecision] = Field(default_factory=list)


class ViewCube(BaseModel):
    join_path: str = Field(description="Exactly one of the offered join paths.")
    includes: list[str] = Field(description="Member names available at that path.")
    prefix: bool = Field(default=False, description="True for joined cubes to avoid name clashes.")


class FolderDecision(BaseModel):
    name: str
    includes: list[str]


class ViewDecision(BaseModel):
    name: str = Field(description="snake_case view name, e.g. sales.")
    description: str
    ai_context: str = Field(description="When to prefer this view, and any caveats.")
    cubes: list[ViewCube]
    folders: list[FolderDecision] = Field(default_factory=list)


class ViewSet(BaseModel):
    views: list[ViewDecision] = Field(default_factory=list)


class Fix(BaseModel):
    aggregation: str = Field(description=f"One of: {', '.join(AGGREGATIONS)}.")
    expression: str = Field(description="Corrected SQL over columns of this table only.")
    give_up: bool = Field(default=False, description="True if this measure cannot be fixed.")


class Enrichment(BaseModel):
    """Everything steps 3 and 4 decided. A model rather than a dataclass so that storing it
    is `model_dump()` and reading it back is `model_validate()` — a new field here survives
    the trip through `praxis.db` without anyone enumerating it."""

    cubes: dict[str, CubeDecision] = Field(default_factory=dict)
    views: list[ViewDecision] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    repaired: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "described_cubes": sum(1 for decision in self.cubes.values() if decision.description),
            "described_members": sum(len(decision.members) for decision in self.cubes.values()),
            "measures": sum(len(decision.measures) for decision in self.cubes.values()),
            "ai_context": sum(
                sum(1 for member in decision.members if member.ai_context)
                + sum(1 for measure in decision.measures if measure.ai_context)
                for decision in self.cubes.values()
            )
            + sum(1 for view in self.views if view.ai_context),
            "views": len(self.views),
            "rejected": len(self.rejected),
        }


# --- the prompts ------------------------------------------------------------


def cube_prompt(table: Table, menu: Menu) -> str:
    dims = []
    for column in table.columns:
        if column.name in table.key_columns:
            continue
        line = f"  {column.name} ({column.type})"
        samples = menu.samples.get(f"{table.name}.{column.name}")
        if samples:
            line += f" e.g. {', '.join(samples[:8])}"
        elif column.null_fraction:
            line += f" — {column.null_fraction:.0%} null"
        dims.append(line)

    # Statistics decide whether a number is worth aggregating, so they go in the prompt.
    candidates = []
    for candidate in menu.for_cube(table.name):
        bits = [f"{candidate.column} ({candidate.data_type})"]
        if candidate.always_same_value:
            bits.append("ALWAYS THE SAME VALUE in every row — summing it equals a row count")
        elif candidate.distinct is not None:
            bits.append(f"n_distinct={candidate.distinct}")
        if candidate.null_fraction:
            bits.append(f"{candidate.null_fraction:.0%} null")
        candidates.append("  " + " — ".join(bits))

    joins = [
        foreign_key.ref_table
        for foreign_key in table.foreign_keys
        if foreign_key.ref_table != table.name
    ]

    return (
        f"Table `{table.qualified}` in a Cube semantic layer.\n\n"
        f"Dimensions already defined:\n" + ("\n".join(dims) or "  (none)") + "\n\n"
        "Numeric columns that could become measures:\n"
        + ("\n".join(candidates) or "  (none)")
        + "\n\n"
        + (f"This cube joins to: {', '.join(joins)}\n\n" if joins else "")
        + "Decide:\n"
        "1. description — what ONE ROW represents.\n"
        "2. members — a description for each dimension above. Use the exact names given.\n"
        "3. measures — only for columns that are genuinely worth aggregating. A column that "
        "is an identifier, a code, a year or a foreign reference is NOT a measure. Prices are "
        "usually avg, not sum. You may combine columns of this table in `expression`.\n\n"
        "Add ai_context only where a reader could pick the wrong member, and say which to "
        "prefer. Omit it otherwise. Never invent or rename a member."
    )


def available_members(
    roots: list[ViewRoot], enriched: dict[str, CubeDecision]
) -> dict[str, set[str]]:
    """join_path -> the members a view may include there."""
    measures = {
        cube: {measure.name for measure in decision.measures} for cube, decision in enriched.items()
    }
    return {
        path: {*members, *measures.get(path.split(".")[-1], ()), "count"}
        for root in roots
        for path, members in root.paths.items()
    }


def views_prompt(roots: list[ViewRoot], enriched: dict[str, CubeDecision]) -> str:
    available = available_members(roots, enriched)
    blocks = []
    for root in roots:
        kind = "FACT TABLE (safe to aggregate)" if root.is_fact else "dimension-like"
        lines = [f"Root `{root.cube}` — {kind}"]
        for path in root.paths:
            lines.append(f"  join_path: {path}  ->  {', '.join(sorted(available[path]))}")
        blocks.append("\n".join(lines))

    return (
        "Design business-oriented Cube views over these roots.\n\n"
        + "\n\n".join(blocks)
        + "\n\nRules:\n"
        "- Prefer FACT TABLE roots: aggregating from a dimension-like root double-counts.\n"
        "- Every join_path in ONE view must start with that view's root. Paths listed under "
        "different roots belong in different views — mixing them loses members.\n"
        "- Use join_path values exactly as offered, and only member names listed for that path.\n"
        "- Set prefix: true for joined cubes so names cannot clash.\n"
        "- Group members into folders a business user would recognise.\n"
        "- ai_context must say when to prefer this view over another, and name any measure "
        "that could be confused with a different one.\n"
        "- One view per coherent business surface. Two or three is usually right; do not "
        "produce a view per table."
    )


# --- the guards -------------------------------------------------------------


@dataclass(frozen=True)
class MeasureRule:
    """One reason a proposed measure may be refused. Ordered most specific first."""

    id: str
    reason: str
    holds: Callable[[MeasureDecision, "MeasureContext"], bool]


@dataclass
class MeasureContext:
    columns: set[str]
    members: set[str]
    candidates: set[str]
    seen: set[str]


IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
SQL_WORDS = frozenset(
    {"CONCAT", "COALESCE", "CASE", "WHEN", "THEN", "ELSE", "END", "NULLIF", "CAST", "AS"}
)


def qualify_expression(expression: str, columns: set[str]) -> str | None:
    """Prefix this table's columns with {CUBE}, or None if a foreign column is named."""
    words = set(re.findall(IDENTIFIER, expression))
    if {word for word in words if word.upper() not in SQL_WORDS and word not in columns}:
        return None
    return re.sub(
        IDENTIFIER,
        lambda m: f"{{CUBE}}.{m.group()}" if m.group() in columns else m.group(),
        expression,
    )


MEASURE_RULES: tuple[MeasureRule, ...] = (
    MeasureRule(
        "aggregation",
        "aggregation is not one of " + ", ".join(AGGREGATIONS),
        lambda m, ctx: m.aggregation in AGGREGATIONS,
    ),
    MeasureRule(
        "candidate",
        "column was not offered as a measure candidate",
        lambda m, ctx: m.column in ctx.candidates,
    ),
    MeasureRule(
        "expression",
        "expression references columns of another table",
        lambda m, ctx: not m.expression or qualify_expression(m.expression, ctx.columns),
    ),
    MeasureRule(
        "name",
        "not a usable member name",
        lambda m, ctx: bool(m.name) and m.name.replace("_", "").isalnum(),
    ),
    MeasureRule(
        "duplicate",
        "name already taken",
        lambda m, ctx: m.name not in ctx.seen and m.name != "count",
    ),
    # Taking its own column's name is fine — that dimension gives way when applied.
    MeasureRule(
        "shadowing",
        "name belongs to a different dimension",
        lambda m, ctx: m.name not in ctx.members or m.name == m.column,
    ),
)


def validate_cube(
    decision: CubeDecision, table: Table, menu: Menu
) -> tuple[CubeDecision, list[str]]:
    """Drop anything the table does not support. Returns the decision and the refusals."""
    context = MeasureContext(
        columns={column.name for column in table.columns},
        members={column.name for column in table.columns if column.name not in table.key_columns},
        candidates={entry.column for entry in menu.for_cube(table.name)},
        seen=set(),
    )
    decision.members = [member for member in decision.members if member.name in context.members]

    rejected: list[str] = []
    kept = []
    for measure in decision.measures:
        broken = next((rule for rule in MEASURE_RULES if not rule.holds(measure, context)), None)
        if broken:
            rejected.append(f"[{broken.id}] {table.name}.{measure.name}: {broken.reason}")
            continue
        context.seen.add(measure.name)
        if measure.expression:
            measure.expression = qualify_expression(measure.expression, context.columns)
        kept.append(measure)
    decision.measures = kept
    return decision, rejected


def validate_paths(
    view: ViewDecision, available: dict[str, set[str]]
) -> tuple[list[ViewCube], list[str]]:
    """Keep a view to one root, and each cube to one path."""
    rejected = [
        f"view {view.name}: unknown join_path {entry.join_path!r}"
        for entry in view.cubes
        if entry.join_path not in available
    ]
    known = [entry for entry in view.cubes if entry.join_path in available]
    if not known:
        return [], rejected

    root = min((entry.join_path for entry in known), key=lambda path: path.count("."))
    cubes: list[ViewCube] = []
    reached: set[str] = set()
    for entry in known:
        if entry.join_path != root and not entry.join_path.startswith(f"{root}."):
            rejected.append(
                f"view {view.name}: {entry.join_path!r} does not descend from root {root!r}"
            )
            continue
        terminal = entry.join_path.split(".")[-1]
        if terminal in reached:
            rejected.append(f"view {view.name}: {terminal} already reached by another path")
            continue
        # Joined cubes must be prefixed, or their members collide with the root's.
        entry.prefix = entry.join_path != root
        entry.includes = [name for name in entry.includes if name in available[entry.join_path]]
        if not entry.includes:
            continue
        reached.add(terminal)
        cubes.append(entry)
    return cubes, rejected


def exposed_members(view: ViewDecision) -> list[tuple[str, str, str, str]]:
    """(join_path, member, exposed name, alias) for every member a view publishes."""
    out: list[tuple[str, str, str, str]] = []
    taken: set[str] = set()
    for entry in view.cubes:
        cube = entry.join_path.split(".")[-1]
        for member in entry.includes:
            name = f"{cube}_{member}" if entry.prefix else member
            alias = ""
            if name in taken:
                candidate = f"{cube}_{member}"
                suffix = 2
                while candidate in taken:
                    candidate = f"{cube}_{member}_{suffix}"
                    suffix += 1
                alias = name = candidate
            taken.add(name)
            out.append((entry.join_path, member, name, alias))
    return out


def validate_views(
    views: list[ViewDecision], roots: list[ViewRoot], enriched: dict[str, CubeDecision]
) -> tuple[list[ViewDecision], list[str]]:
    available = available_members(roots, enriched)
    rejected: list[str] = []
    kept = []
    for view in views:
        if not view.name.replace("_", "").isalnum():
            rejected.append(f"view {view.name!r}: not a usable name")
            continue
        # Cubes and views share one namespace, and Cube's refusal of the clash does not name it —
        # it fails the whole model with "Cannot read properties of undefined".
        if view.name in enriched:
            rejected.append(f"view {view.name}: a cube already has that name")
            continue
        view.cubes, refused = validate_paths(view, available)
        rejected += refused
        if not view.cubes:
            rejected.append(f"view {view.name}: nothing valid to include")
            continue
        exposed = {name for _, _, name, _ in exposed_members(view)}
        for folder in view.folders:
            folder.includes = [name for name in folder.includes if name in exposed]
        view.folders = [folder for folder in view.folders if folder.includes]
        kept.append(view)
    return kept, rejected


# --- the calls --------------------------------------------------------------


@cache
def _cube_model():
    """Built once. A wide schema is one call per table, and constructing a client for
    each of four hundred tables is four hundred clients."""
    return build_model().with_structured_output(CubeDecision)


@cache
def _view_model():
    return build_model().with_structured_output(ViewSet)


async def describe_table(table: Table, menu: Menu, enrichment: Enrichment) -> None:
    """One table's descriptions and measures, recorded on `enrichment`."""
    decision = await _cube_model().ainvoke(cube_prompt(table, menu))
    enrichment.cubes[table.name], rejected = validate_cube(decision, table, menu)
    enrichment.rejected += rejected


async def propose_views(roots: list[ViewRoot], enrichment: Enrichment) -> None:
    """Choose views over the offered join paths, recorded on `enrichment`."""
    proposed = await _view_model().ainvoke(views_prompt(roots, enrichment.cubes))
    enrichment.views, rejected = validate_views(proposed.views, roots, enrichment.cubes)
    enrichment.rejected += rejected


async def repair(enrichment: Enrichment, schema: Schema, errors: dict[str, str]) -> None:
    """One bounded round per failing measure, using Cube's own error text."""
    if not errors:
        return

    model = build_model().with_structured_output(Fix)

    for member, error in errors.items():
        cube_name, _, measure_name = member.partition(".")
        decision = enrichment.cubes.get(cube_name)
        table = schema.table(cube_name)
        if decision is None or table is None or measure_name == "count":
            continue
        target = next(
            (measure for measure in decision.measures if measure.name == measure_name),
            None,
        )
        if target is None:
            continue

        columns = {column.name for column in table.columns}
        fix = await model.ainvoke(
            f"Cube rejected this measure on table `{table.qualified}`.\n\n"
            f"measure: {target.name}\n"
            f"type: {target.aggregation}\n"
            f"sql: {target.expression or target.column}\n\n"
            f"Cube's error:\n{error}\n\n"
            f"Columns available on this table: {', '.join(sorted(columns))}\n\n"
            "Return a corrected aggregation and SQL expression using only these columns, "
            "or set give_up if the measure is not sound."
        )

        qualified = qualify_expression(fix.expression, columns) if fix.expression else ""
        if fix.give_up or fix.aggregation not in AGGREGATIONS or qualified is None:
            decision.measures = [
                measure for measure in decision.measures if measure.name != target.name
            ]
            enrichment.dropped.append(member)
            continue

        target.aggregation = fix.aggregation
        if qualified:
            target.expression = qualified
        enrichment.repaired.append(member)
