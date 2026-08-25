"""What a chart may be, and whether a query's columns can carry one.

Two halves that were two modules: the spec a chart is (`ChartSpec`, `chart_for`,
`validate_spec`) and the rules a saved tile is held to (`CHART_TYPES`, `validate`, `outgrown`).
They are one subject — the second reads the first's limits — and `CHART_TYPES` is exactly what
`ResultChart` draws, so widening the renderer comes before widening the tuple.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import gate
from .catalog import Catalog
from .schema import CubeQuery
from .shape import Column, columns_of

MAX_BARS = 25
MAX_SERIES = 8
MAX_POINTS = 100
MAX_SLICES = 8
MAX_HBARS = 30

NO_X = ("kpi", "gauge")

TIME_X = ("stat", "sparkline", "area", "stacked_area")

MEASURE_X = ("scatter",)

WITH_SERIES = ("line", "area", "stacked_area", "stacked_bar", "grouped_bar", "heatmap")

NEEDS_SERIES = ("heatmap",)


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
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
    ]
    y: list[str] = Field(min_length=1)
    x: str = ""
    series: str = ""
    target: float | None = None


def chart_for(query: dict, catalog: Catalog, rows: list[dict]) -> ChartSpec | None:
    columns = columns_of(query, catalog)
    axes = [column for column in columns if column.kind == "time"]
    dimensions = [column for column in columns if column.kind == "dimension"]
    measures = [column for column in columns if column.kind == "measure"]

    if not measures or not rows or len(rows) > MAX_POINTS:
        return None

    # A second time axis or dimension makes this a cross-tab, and charting one of them
    # silently hides the other.
    if len(axes) > 1 or len(dimensions) > 1:
        return None

    if axes:
        axis = axes[0]
        # No dimension and one distinct dimension value draw the same single line, which a
        # legend would only clutter.
        series = dimensions[0] if dimensions else None
        distinct = len({str(row.get(series.id)) for row in rows}) if series else 1
        if distinct == 1:
            return ChartSpec(type="line", x=axis.id, y=[measures[0].id])
        if distinct <= MAX_SERIES:
            return ChartSpec(type="line", x=axis.id, y=[measures[0].id], series=series.id)
        return None

    if not dimensions:
        # No grouping means Cube returns exactly one row; a KPI drawn from row one of
        # several would be a quiet lie.
        if len(rows) != 1:
            return None
        return ChartSpec(type="kpi", y=[column.id for column in measures])

    if len(rows) <= MAX_BARS:
        return ChartSpec(type="bar", x=dimensions[0].id, y=[measures[0].id])
    return None


def validate_spec(spec: dict, columns: list[Column]) -> tuple[ChartSpec | None, list[str]]:
    try:
        parsed = ChartSpec.model_validate(spec)
        by_id = {column.id: column for column in columns}
        kind, y, x, series = parsed.type, parsed.y, parsed.x, parsed.series
        errors: list[str] = []

        for member_id in y:
            column = by_id.get(member_id)
            if column is None:
                errors.append(f"y column {member_id!r} is not in this result")
            elif column.kind != "measure":
                errors.append(f"y column {member_id!r} is a {column.kind}, not a measure")

        if kind in NO_X:
            if x:
                errors.append(f"{kind} takes no x column")
        elif not x:
            errors.append(f"{kind} needs an x column")
        elif x not in by_id:
            errors.append(f"x column {x!r} is not in this result")
        elif kind in MEASURE_X:
            if by_id[x].kind != "measure":
                errors.append(
                    f"scatter plots one measure against another — x {x!r} is a {by_id[x].kind}"
                )
        elif by_id[x].kind == "measure":
            errors.append(f"x column {x!r} is a measure — use a dimension or a time grain")
        elif kind in TIME_X and by_id[x].kind != "time":
            errors.append(
                f"{kind} needs a time grain on x — {x!r} is a {by_id[x].kind}. "
                "Use bar or hbar for a breakdown by category."
            )

        if series:
            if kind not in WITH_SERIES:
                errors.append(f"series is not meaningful on {kind}")
            elif series not in by_id:
                errors.append(f"series column {series!r} is not in this result")
            elif by_id[series].kind == "measure":
                errors.append(f"series column {series!r} is a measure, not a dimension")
        elif kind in NEEDS_SERIES:
            errors.append(f"{kind} needs a series column — it is the second axis")

        if parsed.target is not None and kind != "gauge":
            errors.append("target is only meaningful on a gauge")

        if errors:
            return None, errors
        return parsed, []
    except ValidationError as exc:
        return None, [_rejection(error) for error in exc.errors()]


def _rejection(error: dict) -> str:
    where = ".".join(str(part) for part in error["loc"]) or "chart"
    return f"{where}: {error['msg'][0].lower()}{error['msg'][1:]}"


# --- what a saved tile is held to ---------------------------------------------

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
