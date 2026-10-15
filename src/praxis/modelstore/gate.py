from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from pydantic import ValidationError

from .catalog import Catalog
from .client import Bound, StoreError
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
    resolved_ranges: dict[str, list[str]] = field(default_factory=dict)

    def as_message(self) -> str:
        parts = []
        if self.errors:
            parts.append("QUERY REJECTED:\n" + "\n".join(f"- {error}" for error in self.errors))
        if self.warnings:
            parts.append("Warnings:\n" + "\n".join(f"- {warning}" for warning in self.warnings))
        return "\n\n".join(parts)


def parse(payload: dict) -> tuple[CubeQuery | None, list[str]]:
    # Tier 1.
    try:
        return CubeQuery.model_validate(payload), []
    except ValidationError as exc:
        messages: list[str] = []
        for error in exc.errors():
            raw_path = [str(part) for part in error["loc"]]
            path = [part for part in raw_path if not part.startswith("function-after[")]
            # A filter is a Filter or a FilterGroup, so pydantic reports against both. A leaf
            # naming `member` was never going to satisfy FilterGroup, and reporting that branch
            # only misdirects the agent's retry.
            names_leaf_field = bool(path) and path[-1] in ("member", "operator", "values")
            if names_leaf_field and "FilterGroup" in " ".join(raw_path):
                continue
            text = f"{'.'.join(path)}: {error['msg']}" if path else error["msg"]
            if text not in messages:
                messages.append(text)
        return None, messages


def semantic_check(query: CubeQuery, catalog: Catalog) -> GateResult:
    # Tier 2 — every check the cached catalog can answer, in one pass. Both of these walk the
    # filter tree, and several checks below want them.
    errors: list[str] = []
    warnings: list[str] = []
    referenced = query.referenced_members()
    leaf_filters = list(query.iter_filters())

    # Does the member exist at all?
    for member_id in referenced:
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
    for time_dimension in query.timeDimensions:
        doc = catalog.get(time_dimension.dimension)
        if doc and doc.type != "time":
            errors.append(
                f"timeDimensions.dimension '{time_dimension.dimension}' is type {doc.type}, "
                "not time"
            )

    # Is the filter operator legal for that member's kind and type?
    for leaf in leaf_filters:
        doc = catalog.get(leaf.member)
        if doc and doc.valid_operators and leaf.operator not in doc.valid_operators:
            errors.append(
                f"operator '{leaf.operator}' is invalid for {leaf.member} "
                f"({doc.kind}, {doc.type}). Valid: {', '.join(doc.valid_operators)}"
            )

    # Recursive, so it stays a function.
    _check_where_having(query.filters, catalog, errors)

    # Does the time dimension offer the requested granularity?
    for time_dimension in query.timeDimensions:
        doc = catalog.get(time_dimension.dimension)
        if doc and time_dimension.granularity and doc.granularities:
            if time_dimension.granularity not in doc.granularities:
                errors.append(
                    f"granularity '{time_dimension.granularity}' unavailable on "
                    f"{time_dimension.dimension}. Available: {', '.join(doc.granularities)}"
                )

    # Reaching across entities with no view covering them may not be joinable.
    known = [member_id for member_id in referenced if catalog.get(member_id)]
    cubes = {catalog.get(member_id).cube for member_id in known}
    if len(cubes) > 1 and not catalog.views_containing(known):
        warnings.append(
            f"members span {len(cubes)} entities ({', '.join(sorted(cubes))}) with no single "
            "view containing them all — verify they are joinable before trusting the result."
        )

    for leaf in leaf_filters:
        if leaf.operator in ("inDateRange", "notInDateRange"):
            warnings.append(
                f"'{leaf.operator}' on {leaf.member} blocks pre-aggregation matching. "
                "Prefer timeDimensions.dateRange."
            )

    # `is not None`, so this fires only on a limit the agent actually asked for.
    if query.limit is not None and not query.order:
        warnings.append(
            "limit set without order — results may be silently truncated. Set an explicit order."
        )
    return GateResult(ok=not errors, errors=errors, warnings=warnings)


async def check(payload: dict, catalog: Catalog, client: Bound, max_rows: int) -> GateResult:
    # Tiers 1-3. An ok result carries the parsed query and Cube's SQL, and executed nothing.
    query, errors = parse(payload)
    if query is None:
        return GateResult(ok=False, errors=errors)

    checked = semantic_check(query, catalog)
    if not checked.ok:
        return checked

    # After semantic_check, so the limit-without-order warning sees what the agent asked for.
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


async def execute(checked: GateResult, client: Bound) -> GateResult:
    # Tier 4. Takes the GateResult so tiers 2-3's SQL and warnings survive rather than being
    # compiled a second time.
    if not checked.ok or checked.query is None:
        raise ValueError("execute() takes a GateResult that passed check()")
    try:
        loaded = await client.load(checked.query)
        return GateResult(
            ok=True,
            warnings=checked.warnings,
            sql=checked.sql,
            rows=loaded.rows,
            duration_ms=loaded.duration_ms,
            query=checked.query,
            resolved_ranges=loaded.resolved_ranges,
        )
    except StoreError as exc:
        return GateResult(ok=False, errors=[f"Execution failed: {exc}"], sql=checked.sql)


async def pages(
    checked: GateResult, client: Bound, size: int, most: int
) -> AsyncIterator[list[dict]]:
    """Tier 4 for a result too large for one request: the rows, `size` at a time, until a page
    comes back short or `most` rows have been read.

    Cube answers at most 50,000 rows to one request, so a larger result is read by `offset`.
    That is safe only under a total order, and a Cube query always has one available: there is
    no `ungrouped`, so no two rows share every dimension value. The dimensions are appended after
    whatever order was asked for — a measure alone can tie, and a tie lets a row move between
    pages, so one page repeats it and the next skips it.
    """
    if not checked.ok or checked.query is None:
        raise ValueError("pages() takes a GateResult that passed check()")
    query = checked.query.model_copy(deep=True)
    order = dict(query.order or {})
    # A time dimension is a column only when it has a granularity; a bare dateRange narrows
    # and adds nothing to order by.
    grouped = [td.dimension for td in query.timeDimensions if td.granularity]
    for member in [*grouped, *query.dimensions]:
        order.setdefault(member, "asc")
    query.order = order or None

    read = 0
    while read < most:
        query.limit = min(size, most - read)
        query.offset = checked.query.offset + read
        rows = (await client.load(query)).rows
        read += len(rows)
        yield rows
        if len(rows) < query.limit:
            return


async def run(payload: dict, catalog: Catalog, client: Bound, max_rows: int) -> GateResult:
    # All four tiers — what a chat turn wants: one query, executed or explained.
    checked = await check(payload, catalog, client, max_rows)
    if not checked.ok:
        return checked
    return await execute(checked, client)


def _check_where_having(nodes: list[FilterNode], catalog: Catalog, errors: list[str]) -> None:
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
            operator = "and" if node.and_ else "or"
            errors.append(
                f"'{operator}' group mixes measure and dimension filters — Cube forbids this "
                "(WHERE vs HAVING). Split them into separate top-level filters."
            )
        _check_where_having(node.children, catalog, errors)
