"""What a result set looks like, derived from the query. The one place deciding column order,
labels, alignment and format — `visualize.chart_for` reads these kinds rather than re-deriving.
"""

from dataclasses import dataclass

from .catalog import Catalog

GRAINS = ("year", "quarter", "month", "week", "day", "hour", "minute", "second")
"""Cube's granularities, longest first. Paired with `GRAINS` in `src/web/src/format.ts` —
a grain added to one belongs in the other.
"""


@dataclass
class Column:
    """One column, ready to render. `id` is the row-dict key: Cube appends the grain to a
    grouped time dimension, so a column id need not name a member the catalog knows.
    """

    id: str
    label: str
    kind: str
    """time | dimension | measure — what the column is *for*, not Cube's member kind. `time` is
    a grouped `timeDimensions` entry, the only thing a line chart can use as an x axis.
    """
    type: str
    format: str
    """Cube's display format for a measure — `currency`, `percent`, or empty."""
    align: str


def label_of(member_id: str, catalog: Catalog) -> str:
    """A column heading: Cube's short title, else the bare member name. Not `describe._title`,
    which lowercases for mid-sentence use.
    """
    doc = catalog.get(member_id)
    if doc and doc.title:
        return doc.title
    return member_id.split(".")[-1].replace("_", " ")


def export_value(value: object, column: Column) -> str:
    """One CSV cell. Numbers stay raw for the spreadsheet, and a grain of a day or longer loses
    the `T00:00:00.000` Cube pads it with.
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
    """The header for `query`'s result: time axis, then dimensions, then measures — labels
    before numbers, the opposite of the order a Cube query lists them in.
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
