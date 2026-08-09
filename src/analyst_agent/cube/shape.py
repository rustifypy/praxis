"""What a result set looks like, derived from the query that produced it.

The same argument as `describe.py`: a Cube query is structured data, so its result's
header is deterministic, free, testable, and cannot describe a column that was never
requested. Nothing here asks the model anything.

This is the one place that decides column order, labels, alignment and number format, and
it has two callers — the stored result the browser renders, and `visualize.chart_for`,
which picks a chart by reading these kinds rather than re-deriving them. One rule, one
place: a header that disagreed with the chart's idea of which column held the measure
would put the wrong series on the wrong axis.
"""

from dataclasses import dataclass

from .catalog import Catalog

GRAINS = ("year", "quarter", "month", "week", "day", "hour", "minute", "second")
"""Cube's time granularities, longest period first. Mirrored by `GRAINS` in
`src/web/src/format.ts`, which reads the same suffix to label an axis — the two are a pair,
and a grain added to one belongs in the other."""


@dataclass
class Column:
    """One column of a result set, ready to render.

    `id` is the key in every row dict. Cube returns fully qualified member ids, and for a
    time dimension grouped by a granularity it appends the grain — `invoice.date.month` —
    so a column id cannot be assumed to name a member the catalog knows.
    """

    id: str
    label: str
    kind: str
    """time | dimension | measure — what the column is *for*, not Cube's member kind.

    `time` means a grouped `timeDimensions` entry, and nothing else: it is the one thing a
    line chart can use as an x axis. A `dimensions` entry of type `time` has no grain, so
    it is a detail column — `kind` is `dimension` and `type` is what says to format it as
    a date."""
    type: str
    format: str
    """Cube's display format for a measure — `currency`, `percent`, or empty."""
    align: str


def label_of(member_id: str, catalog: Catalog) -> str:
    """A column heading: Cube's own short title, else the bare member name.

    Deliberately not `describe._title`, which lowercases for use mid-sentence. A heading
    keeps the case Cube was given.
    """
    doc = catalog.get(member_id)
    if doc and doc.title:
        return doc.title
    return member_id.split(".")[-1].replace("_", " ")


def export_value(value: object, column: Column) -> str:
    """One cell for a CSV.

    Numbers stay raw — a spreadsheet wants 12404.50, not "$12,404.50", and grouping
    separators would make the column text in half the world's locales. Dates lose the
    `T00:00:00.000` that Cube pads every grain of a day or longer with, because a column
    headed "Year" holding `2021-01-01T00:00:00.000` reads as a bug. What is left is still
    ISO, so it parses everywhere.
    """
    if value is None:
        return ""
    if column.kind == "time" or column.type == "time":
        text = str(value)
        grain = column.id.rsplit(".", 1)[-1]
        # An hour bucket at midnight is also "…T00:00:00.000", so the grain decides,
        # not the value.
        if "T" in text and grain in ("year", "quarter", "month", "week", "day"):
            return text.split("T")[0]
        return text
    return str(value)


def columns_of(query: dict, catalog: Catalog) -> list[Column]:
    """The header for `query`'s result, in the order a reader wants it.

    Time axis, then the other dimensions, then the measures — labels before numbers, which
    is the opposite of the order a Cube query lists them in. `describe_query` draws the
    same line for its sentence ("revenue by month"), and the two agreeing is why a chart's
    axis titles match the table's headings.
    """
    times: list[Column] = []
    dimensions: list[Column] = []
    measures: list[Column] = []

    grouped = [
        td
        for td in query.get("timeDimensions") or []
        # A bare dateRange narrows the result without adding a column. Only a granularity
        # groups by time, and Cube keys that column by member id plus the grain.
        if td.get("dimension") and td.get("granularity")
    ]
    for td in grouped:
        member_id = td["dimension"]
        grain = str(td["granularity"])
        # "Month" reads better than "Invoiced at (month)" and is unambiguous while there is
        # one time axis. Two of them share a grain often enough — a cohort date against an
        # event date — that the member has to be named.
        if len(grouped) == 1:
            heading = grain.title()
        else:
            heading = f"{label_of(member_id, catalog)} ({grain})"
        times.append(
            Column(
                id=f"{member_id}.{grain}",
                label=heading,
                kind="time",
                type="time",
                format="",
                align="left",
            )
        )

    for member_id in query.get("dimensions") or []:
        doc = catalog.get(member_id)
        dimensions.append(
            Column(
                id=member_id,
                label=label_of(member_id, catalog),
                # A time dimension listed here has no granularity, so it is a detail column
                # and not an axis — `kind` stays `dimension` and `type` carries the fact
                # that it needs date formatting. `chart_for` reads `kind` to find the x
                # axis, and a raw timestamp per row is not one.
                kind="dimension",
                type=doc.type if doc else "",
                format="",
                align="left",
            )
        )

    for member_id in query.get("measures") or []:
        doc = catalog.get(member_id)
        measures.append(
            Column(
                id=member_id,
                label=label_of(member_id, catalog),
                kind="measure",
                type=doc.type if doc else "",
                format=doc.format if doc else "",
                align="right",
            )
        )

    return [*times, *dimensions, *measures]
