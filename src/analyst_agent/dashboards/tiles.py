from dataclasses import dataclass, field

from ..cube import gate
from ..cube.catalog import Catalog
from ..cube.schema import CubeQuery
from ..cube.shape import Column, columns_of
from ..cube.visualize import (
    MAX_BARS,
    MAX_HBARS,
    MAX_POINTS,
    MAX_SERIES,
    MAX_SLICES,
    validate_spec,
)

CHART_TYPES = (
    "line",
    "area",
    "stacked_area",
    "bar",
    "stacked_bar",
    "grouped_bar",
    "hbar",
    "donut",
    "scatter",
    "heatmap",
    "gauge",
    "sparkline",
    "kpi",
    "stat",
    "table",
)


@dataclass
class TileCheck:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    time_member: str = ""


def primary_time_member(query: CubeQuery) -> str:
    # The grouped one wins: narrowing anything else changes the numbers, not the picture.
    for time_dimension in query.timeDimensions:
        if time_dimension.granularity:
            return time_dimension.dimension
    for time_dimension in query.timeDimensions:
        if time_dimension.dateRange is not None:
            return time_dimension.dimension
    return ""


def window_warnings(query: CubeQuery) -> list[str]:
    # A warning and not a rejection: a pinned tile is legitimate, and the surprise is the
    # failure.
    warnings: list[str] = []
    for time_dimension in query.timeDimensions:
        window = time_dimension.dateRange
        if isinstance(window, list) and len(window) == 2:
            warnings.append(
                f"the window on {time_dimension.dimension} is fixed to {window[0]} – "
                f"{window[1]}, so refreshing will not move it. Use a relative range like "
                '"last 30 days" for a tile that should follow the calendar.'
            )
    return warnings


def validate(query_payload: dict, chart: dict, catalog: Catalog) -> TileCheck:
    # Tiers 1-2, then the chart against the header that query will produce. Tier 3 is
    # `gate.check`, split off so authoring costs no round trip per attempt.
    query, errors = gate.parse(query_payload)
    if query is None:
        return TileCheck(ok=False, errors=errors)

    semantic = gate.semantic_check(query, catalog)
    if not semantic.ok:
        return TileCheck(ok=False, errors=semantic.errors, warnings=semantic.warnings)

    warnings = [*semantic.warnings, *window_warnings(query)]
    columns = columns_of(query.to_payload(), catalog)

    kind = str(chart.get("type", ""))
    if kind not in CHART_TYPES:
        return TileCheck(
            ok=False,
            errors=[f"chart type must be one of {', '.join(CHART_TYPES)} — got {kind!r}"],
            warnings=warnings,
            columns=columns,
        )

    # A table draws every column it was given, so there is no spec to disagree with. Everything
    # else goes through the same `validate_spec` that `set_chart` uses, so a tile and a
    # transcript chart cannot disagree about what a legal y column is.
    if kind != "table":
        checked, chart_errors = validate_spec(chart, columns)
        if checked is None:
            return TileCheck(ok=False, errors=chart_errors, warnings=warnings, columns=columns)

    return TileCheck(
        ok=True,
        warnings=warnings,
        columns=columns,
        time_member=primary_time_member(query),
    )


def outgrown(chart: dict, columns: list[Column], rows: list[dict]) -> str:
    # Recomputed every refresh and every read: the verdict depends on the rows, so it cannot
    # be stored.
    kind = str(chart.get("type", ""))
    if kind == "table":
        return ""
    if not rows:
        return "the query returned no rows"

    if kind in ("kpi", "gauge"):
        # A tile showing row one of many would be a quiet lie about which row it picked.
        if len(rows) != 1:
            return f"a {kind} needs a single row and this returned {len(rows)}"
        return ""

    if len(rows) > MAX_POINTS:
        return (
            f"{len(rows)} rows is past the {MAX_POINTS} a chart is drawn from, "
            "so the picture would show a prefix of the data"
        )

    if kind in ("bar", "stacked_bar", "grouped_bar", "hbar"):
        # Bars drawn, not rows read: a stacked or grouped bar carries one row per series per
        # bar, so a row count would degrade a chart that is twenty legible columns wide.
        axis = str(chart.get("x", ""))
        drawn = len({str(row.get(axis)) for row in rows}) if axis else len(rows)
        series_column = str(chart.get("series", ""))
        if kind == "grouped_bar" and series_column:
            drawn *= len({str(row.get(series_column)) for row in rows})
        if kind == "hbar":
            if drawn > MAX_HBARS:
                return f"{drawn} bars is past the {MAX_HBARS} a horizontal chart stays readable at"
        elif drawn > MAX_BARS:
            return f"{drawn} bars is past the {MAX_BARS} that stay legible"

    if kind == "donut":
        if len(rows) > MAX_SLICES:
            return f"{len(rows)} slices is past the {MAX_SLICES} a donut can label"
        measure = (chart.get("y") or [""])[0]
        for row in rows:
            try:
                if float(row.get(measure) or 0) < 0:
                    return "a donut cannot show a negative value as a share of a whole"
            except (TypeError, ValueError):
                continue

    series = str(chart.get("series", ""))
    if series:
        by_id = {column.id: column for column in columns}
        if series not in by_id:
            return f"the series column {series!r} is no longer in this result"
        distinct = len({str(row.get(series)) for row in rows})
        if distinct > MAX_SERIES:
            return (
                f"{distinct} series is past the {MAX_SERIES} the palette holds without "
                "repeating a colour"
            )
    return ""
