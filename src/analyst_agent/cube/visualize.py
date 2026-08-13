"""Which chart a result should be drawn as, derived from the query. **`None` is a correct and
common answer**; one measure per line or bar, since mixed magnitudes flatten the smaller.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .catalog import Catalog
from .shape import Column, columns_of

MAX_BARS = 25
"""Above this a bar chart is a picket fence. `order` plus `limit` is how a question asks for
a top-N; the answer to "revenue by customer" over 300 customers is the table.
"""

MAX_SERIES = 8
"""Distinct dimension values before lines stop being legible, and the palette's slot count
— a ninth series is refused rather than drawn in a repeated colour.
"""

MAX_POINTS = 100
"""Rows a chart may be drawn from, and the rows a `tool_end` event carries. One constant, or
a chart would plot the first hundred of four hundred months as if it were the series.
"""


class ChartSpec(BaseModel):
    """What to draw. Column ids only — the renderer already has the header for labels and
    number formats, and duplicating them here is how the two would come to disagree.

    A model rather than a dataclass because `set_chart` is handed one by the agent: a bare
    string for `y` used to be iterated character by character, and a misspelled key was
    accepted in silence.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["line", "bar", "kpi"]
    y: list[str]
    x: str = ""
    series: str = ""
    """Column whose values split `y` into one line each. Empty for a single series."""


def chart_for(query: dict, catalog: Catalog, rows: list[dict]) -> ChartSpec | None:
    """The chart for this result, or `None` when the table is the better answer. `rows` is read
    for cardinality only — guessing bar count from `limit` gave an unlimited query 300 bars.
    """
    columns = columns_of(query, catalog)
    axes = [c for c in columns if c.kind == "time"]
    dimensions = [c for c in columns if c.kind == "dimension"]
    measures = [c for c in columns if c.kind == "measure"]

    # `MAX_POINTS` is also how many rows the event carries, so this is what guarantees the
    # browser can draw the whole chart from what it already has.
    if not measures or not rows or len(rows) > MAX_POINTS:
        return None

    # Two time axes is a cross-tab, and a chart of one of them silently hides the other.
    if len(axes) > 1 or len(dimensions) > 1:
        return None

    if axes:
        axis = axes[0]
        if not dimensions:
            return ChartSpec(type="line", x=axis.id, y=[measures[0].id])
        series = dimensions[0]
        count = len({str(row.get(series.id)) for row in rows})
        # One distinct value is a single line that a legend would only clutter.
        if count == 1:
            return ChartSpec(type="line", x=axis.id, y=[measures[0].id])
        if count <= MAX_SERIES:
            return ChartSpec(type="line", x=axis.id, y=[measures[0].id], series=series.id)
        return None

    if not dimensions:
        # No grouping means Cube returns exactly one row. Anything else is a shape this
        # does not understand, and a KPI tile of row one would be a quiet lie.
        if len(rows) != 1:
            return None
        return ChartSpec(type="kpi", y=[c.id for c in measures])

    if len(rows) <= MAX_BARS:
        return ChartSpec(type="bar", x=dimensions[0].id, y=[measures[0].id])
    return None


def validate_spec(spec: dict, columns: list[Column]) -> tuple[ChartSpec | None, list[str]]:
    """Check an agent-authored spec against the columns the result actually has. A rejection is
    text the agent can act on, never an exception.

    `ChartSpec` owns the shape — the types, the closed set of chart types, `y` being a non-empty
    list, and refusing a key it does not have. What is left here is the half a model cannot
    check: whether these column ids exist in *this* result, and what kind each one is.
    """
    try:
        parsed = ChartSpec.model_validate(spec)
    except ValidationError as exc:
        return None, [_rejection(e) for e in exc.errors()]

    by_id = {c.id: c for c in columns}
    kind, y, x, series = parsed.type, parsed.y, parsed.x, parsed.series
    errors: list[str] = []

    if not y:
        errors.append("y must name at least one measure column")
    for member_id in y:
        column = by_id.get(member_id)
        if column is None:
            errors.append(f"y column {member_id!r} is not in this result")
        elif column.kind != "measure":
            errors.append(f"y column {member_id!r} is a {column.kind}, not a measure")

    if kind == "kpi":
        if x:
            errors.append("kpi takes no x column")
    elif not x:
        errors.append(f"{kind} needs an x column")
    elif x not in by_id:
        errors.append(f"x column {x!r} is not in this result")
    elif by_id[x].kind == "measure":
        errors.append(f"x column {x!r} is a measure — use a dimension or a time grain")

    if series:
        if kind != "line":
            errors.append("series is only meaningful on a line chart")
        elif series not in by_id:
            errors.append(f"series column {series!r} is not in this result")
        elif by_id[series].kind == "measure":
            errors.append(f"series column {series!r} is a measure, not a dimension")

    if errors:
        return None, errors
    return parsed, []


def _rejection(error: dict) -> str:
    """One pydantic error as a line the agent can act on. The field path matters more than the
    wording: `y` being a bare string has to read as a type problem, not as a bad column name."""
    where = ".".join(str(part) for part in error["loc"]) or "chart"
    return f"{where}: {error['msg'][0].lower()}{error['msg'][1:]}"
