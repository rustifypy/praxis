from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .catalog import Catalog
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
