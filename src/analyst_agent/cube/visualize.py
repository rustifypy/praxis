"""Which chart a result should be drawn as, derived from the query that produced it.

`describe.py` writes the sentence, `shape.py` writes the header, and this writes the
picture — all three from the query itself rather than by asking the model. That is what
makes a chart free, instant on a replayed transcript, and incapable of plotting a column
nobody fetched.

**Returning `None` is a correct answer and the common one.** Two dimensions crossed, forty
categories, a raw timestamp per row — those are tables, and a chart drawn of them is worse
than no chart. The agent can override with `set_chart` when it disagrees; the point of the
default is that it is never wrong, not that it is always present.

One measure per line or bar, deliberately. Revenue in thousands and order count in tens
share an axis badly enough that the smaller series becomes a flat line on zero, and
`format` does not separate them — two currency measures can differ by three orders of
magnitude just as easily. So a multi-measure query plots its first measure and the spec
says which; the table beside it carries them all.
"""

from dataclasses import dataclass, field

from .catalog import Catalog
from .shape import Column, columns_of

MAX_BARS = 25
"""Above this a bar chart is a picket fence with unreadable labels. `order` plus `limit` is
how a question asks for a top-N, and the answer to "revenue by customer" over 300 customers
is the table."""

MAX_SERIES = 8
"""Distinct values a dimension may have before it stops being a legible set of lines.

Also the size of the renderer's categorical palette, which is validated to eight slots and
never cycles — so a ninth series is refused here rather than drawn in a repeated colour."""

MAX_POINTS = 100
"""Rows a chart may be drawn from, and the number of rows the `tool_end` event carries.

One constant for both on purpose. The browser draws from the event so a replayed transcript
needs no requests, and a chart offered for more rows than the event holds would quietly
plot the first hundred of four hundred months as if that were the whole series."""


@dataclass
class ChartSpec:
    """What to draw. Column ids only — the renderer already has the header for labels and
    number formats, and duplicating them here is how the two would come to disagree."""

    type: str
    """line | bar | kpi"""
    y: list[str] = field(default_factory=list)
    x: str = ""
    series: str = ""
    """Column whose values split `y` into one line each. Empty for a single series."""


def _distinct(rows: list[dict], column: Column) -> int:
    return len({str(row.get(column.id)) for row in rows})


def chart_for(query: dict, catalog: Catalog, rows: list[dict]) -> ChartSpec | None:
    """The chart for this result, or `None` when the table is the better answer.

    `rows` is read for cardinality only — how many bars, how many series. Those are
    properties of the data rather than the query, and guessing them from `limit` was how a
    query with no limit at all got a chart with three hundred bars.
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
        count = _distinct(rows, series)
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
    """Check an agent-authored spec against the columns the result actually has.

    The same shape of guard as the query gate: a rejection comes back as text the agent can
    act on, never an exception, and a chart is never drawn from a column id that was not in
    the result. Nothing here trusts that the model read the header correctly.
    """
    by_id = {c.id: c for c in columns}
    kind = str(spec.get("type", ""))
    errors: list[str] = []

    if kind not in ("line", "bar", "kpi"):
        errors.append(f"type must be line, bar or kpi — got {kind!r}")

    y = [str(m) for m in spec.get("y") or []]
    if not y:
        errors.append("y must name at least one measure column")
    for member_id in y:
        column = by_id.get(member_id)
        if column is None:
            errors.append(f"y column {member_id!r} is not in this result")
        elif column.kind != "measure":
            errors.append(f"y column {member_id!r} is a {column.kind}, not a measure")

    x = str(spec.get("x", ""))
    if kind == "kpi":
        if x:
            errors.append("kpi takes no x column")
    elif not x:
        errors.append(f"{kind} needs an x column")
    elif x not in by_id:
        errors.append(f"x column {x!r} is not in this result")
    elif by_id[x].kind == "measure":
        errors.append(f"x column {x!r} is a measure — use a dimension or a time grain")

    series = str(spec.get("series", ""))
    if series:
        if kind != "line":
            errors.append("series is only meaningful on a line chart")
        elif series not in by_id:
            errors.append(f"series column {series!r} is not in this result")
        elif by_id[series].kind == "measure":
            errors.append(f"series column {series!r} is a measure, not a dimension")

    if errors:
        return None, errors
    return ChartSpec(type=kind, x=x, y=y, series=series), []
