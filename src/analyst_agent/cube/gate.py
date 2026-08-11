"""Four-tier query gate.

1. schema    — pydantic, in CubeQuery
2. semantic  — local, against the cached catalog (this module)
3. compile   — POST /v1/sql, Cube's own compiler, nothing executed
4. execute   — POST /v1/load

Failures return text for the agent to act on, never exceptions. `run` is all four tiers;
`check`/`execute` expose the seam for callers that must compile without executing.
"""

from dataclasses import dataclass, field

from pydantic import ValidationError

from .catalog import Catalog
from .client import CubeClient, CubeError
from .schema import CubeQuery, Filter, FilterGroup, FilterNode


@dataclass
class GateResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sql: str | None = None
    rows: list[dict] | None = None
    duration_ms: int | None = None
    query: CubeQuery | None = None
    """The parsed, limit-capped query, set once tier 1 passes. `execute` reads it rather than
    re-parsing the payload."""
    resolved_ranges: dict[str, list[str]] = field(default_factory=dict)
    """Tier 4 only — see `client.LoadResult.resolved_ranges`."""

    def as_message(self) -> str:
        parts = []
        if self.errors:
            parts.append("QUERY REJECTED:\n" + "\n".join(f"- {e}" for e in self.errors))
        if self.warnings:
            parts.append("Warnings:\n" + "\n".join(f"- {w}" for w in self.warnings))
        return "\n\n".join(parts)


def parse(payload: dict) -> tuple[CubeQuery | None, list[str]]:
    """Tier 1. A filter is a Filter or a FilterGroup, so pydantic reports failures for both;
    the dead branch is dropped before it reaches the agent's retry.
    """
    try:
        return CubeQuery.model_validate(payload), []
    except ValidationError as e:
        out: list[str] = []
        for err in e.errors():
            raw = [str(p) for p in err["loc"]]
            path = [p for p in raw if not p.startswith("function-after[")]
            # A leaf filter naming `member` was never going to satisfy FilterGroup;
            # complaining that FilterGroup rejects `member` only misdirects the retry.
            leaf_field = bool(path) and path[-1] in ("member", "operator", "values")
            if leaf_field and "FilterGroup" in " ".join(raw):
                continue
            text = f"{'.'.join(path)}: {err['msg']}" if path else err["msg"]
            if text not in out:
                out.append(text)
        return None, out


def semantic_check(query: CubeQuery, catalog: Catalog) -> GateResult:
    """Tier 2. Every check the cached catalog can answer, in one pass."""
    errors: list[str] = []
    warnings: list[str] = []

    # Does the member exist at all?
    for member_id in query.referenced_members():
        if catalog.get(member_id) is None:
            hint = ", ".join(catalog.nearest(member_id)) or "none"
            errors.append(f"'{member_id}' does not exist. Closest matches: {hint}")

    # Is it the kind of member this field accepts?
    for field_name, expected in (
        ("measures", "measure"),
        ("dimensions", "dimension"),
        ("segments", "segment"),
    ):
        for member_id in getattr(query, field_name):
            doc = catalog.get(member_id)
            if doc and doc.kind != expected:
                errors.append(f"'{member_id}' is a {doc.kind}, not a {expected}")
    for td in query.timeDimensions:
        doc = catalog.get(td.dimension)
        if doc and doc.type != "time":
            errors.append(f"timeDimensions.dimension '{td.dimension}' is type {doc.type}, not time")

    # Is the filter operator legal for that member's kind and type?
    for f in query.iter_filters():
        doc = catalog.get(f.member)
        if doc and doc.valid_operators and f.operator not in doc.valid_operators:
            errors.append(
                f"operator '{f.operator}' is invalid for {f.member} ({doc.kind}, {doc.type}). "
                f"Valid: {', '.join(doc.valid_operators)}"
            )

    # Recursive, so it stays a function.
    _check_where_having(query.filters, catalog, errors)

    # Does the time dimension offer the requested granularity?
    for td in query.timeDimensions:
        doc = catalog.get(td.dimension)
        if doc and td.granularity and doc.granularities:
            if td.granularity not in doc.granularities:
                errors.append(
                    f"granularity '{td.granularity}' unavailable on {td.dimension}. "
                    f"Available: {', '.join(doc.granularities)}"
                )

    # Reaching across entities with no view covering them may not be joinable.
    known = [m for m in query.referenced_members() if catalog.get(m)]
    cubes = {catalog.get(m).cube for m in known}
    if len(cubes) > 1 and not catalog.views_containing(known):
        warnings.append(
            f"members span {len(cubes)} entities ({', '.join(sorted(cubes))}) with no single "
            "view containing them all — verify they are joinable before trusting the result."
        )

    for f in query.iter_filters():
        if f.operator in ("inDateRange", "notInDateRange"):
            warnings.append(
                f"'{f.operator}' on {f.member} blocks pre-aggregation matching. "
                "Prefer timeDimensions.dateRange."
            )

    # `is not None`, so this fires on a limit the agent actually asked for. While the
    # schema defaulted it, every query carried this warning and the prompt tells the
    # agent to report warnings to the user.
    if query.limit is not None and not query.order:
        warnings.append(
            "limit set without order — results may be silently truncated. Set an explicit order."
        )
    return GateResult(ok=not errors, errors=errors, warnings=warnings)


async def check(payload: dict, catalog: Catalog, client: CubeClient, max_rows: int) -> GateResult:
    """Tiers 1-3. An ok result carries the parsed query and Cube's SQL, and executed nothing."""
    query, errors = parse(payload)
    if query is None:
        return GateResult(ok=False, errors=errors)

    checked = semantic_check(query, catalog)
    if not checked.ok:
        return checked

    # Applied after semantic_check so the warning above sees what the agent asked for.
    # An absent limit still caps at max_rows — the row ceiling is not optional.
    query.limit = max_rows if query.limit is None else min(query.limit, max_rows)

    compiled = await client.compile(query)
    if not compiled.ok:
        return GateResult(
            ok=False,
            errors=[f"Cube could not compile this query: {compiled.error}"],
            warnings=checked.warnings,
        )

    return GateResult(ok=True, warnings=checked.warnings, sql=compiled.sql, query=query)


async def execute(checked: GateResult, client: CubeClient) -> GateResult:
    """Tier 4. Takes the `GateResult` so tiers 2-3's SQL and warnings survive into the
    executed result rather than being compiled twice."""
    if not checked.ok or checked.query is None:
        raise ValueError("execute() takes a GateResult that passed check()")
    try:
        loaded = await client.load(checked.query)
    except CubeError as e:
        return GateResult(ok=False, errors=[f"Execution failed: {e}"], sql=checked.sql)

    return GateResult(
        ok=True,
        warnings=checked.warnings,
        sql=checked.sql,
        rows=loaded.rows,
        duration_ms=loaded.duration_ms,
        query=checked.query,
        resolved_ranges=loaded.resolved_ranges,
    )


async def run(payload: dict, catalog: Catalog, client: CubeClient, max_rows: int) -> GateResult:
    """All four tiers. What a chat turn wants: one query, executed or explained."""
    checked = await check(payload, catalog, client, max_rows)
    if not checked.ok:
        return checked
    return await execute(checked, client)


def _check_where_having(nodes: list[FilterNode], catalog: Catalog, errors: list[str]) -> None:
    """Cube forbids dimension and measure filters in the same logical operator:
    they compile to WHERE and HAVING respectively."""
    for node in nodes:
        if not isinstance(node, FilterGroup):
            continue
        kinds = set()
        for child in node.children:
            if isinstance(child, Filter):
                doc = catalog.get(child.member)
                if doc:
                    kinds.add(doc.kind)
        if {"measure", "dimension"} <= kinds:
            op = "and" if node.and_ else "or"
            errors.append(
                f"'{op}' group mixes measure and dimension filters — Cube forbids this "
                "(WHERE vs HAVING). Split them into separate top-level filters."
            )
        _check_where_having(node.children, catalog, errors)
