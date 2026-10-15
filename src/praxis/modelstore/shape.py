from dataclasses import dataclass

from .catalog import Catalog

DATE_GRAINS = ("year", "quarter", "month", "week", "day")


@dataclass
class Column:
    id: str
    label: str
    kind: str
    type: str
    format: str
    align: str


def label_of(member_id: str, catalog: Catalog) -> str:
    # Not `_title`, which lowercases for mid-sentence use.
    doc = catalog.get(member_id)
    if doc and doc.title:
        return doc.title
    return member_id.split(".")[-1].replace("_", " ")


def export_value(value: object, column: Column) -> str:
    if value is None:
        return ""
    if column.kind == "time" or column.type == "time":
        text = str(value)
        grain = column.id.rsplit(".", 1)[-1]
        # An hour bucket at midnight is also "…T00:00:00.000", so the grain decides and
        # not the value.
        if "T" in text and grain in DATE_GRAINS:
            return text.split("T")[0]
        return text
    return str(value)


def columns_of(query: dict, catalog: Catalog) -> list[Column]:
    times: list[Column] = []
    dimensions: list[Column] = []
    measures: list[Column] = []

    # A bare dateRange narrows the result without adding a column; only a granularity groups
    # by time, and Cube keys that column by member id plus the grain.
    grouped = [
        time_dimension
        for time_dimension in query.get("timeDimensions") or []
        if time_dimension.get("dimension") and time_dimension.get("granularity")
    ]
    for time_dimension in grouped:
        member_id = time_dimension["dimension"]
        grain = str(time_dimension["granularity"])
        # "Month" beats "Invoiced at (month)" while there is one time axis, but two axes
        # sharing a grain — a cohort date against an event date — have to name the member.
        heading = (
            grain.title() if len(grouped) == 1 else f"{label_of(member_id, catalog)} ({grain})"
        )
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
        # A time dimension listed here has no granularity, so it is a detail column and not an
        # axis: `chart_for` reads `kind` to find the x axis, and a raw timestamp per row is not
        # one. `type` still carries the fact that it needs date formatting.
        dimensions.append(
            Column(
                id=member_id,
                label=label_of(member_id, catalog),
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


def _title(member_id: str, catalog: Catalog | None) -> str:
    doc = catalog.get(member_id) if catalog else None
    if doc and doc.title:
        return doc.title.lower()
    return member_id.split(".")[-1].replace("_", " ")


def _join(parts: list[str]) -> str:
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


def describe_query(query: dict, catalog: Catalog | None = None) -> str:
    measures = [_title(member, catalog) for member in query.get("measures") or []]
    groups = [_title(member, catalog) for member in query.get("dimensions") or []]
    notes: list[str] = []

    for time_dimension in query.get("timeDimensions") or []:
        if time_dimension.get("granularity"):
            groups.append(str(time_dimension["granularity"]))
        if window := time_dimension.get("dateRange"):
            notes.append(f"{window[0]} to {window[1]}" if isinstance(window, list) else str(window))

    head = _join(measures) or _join(groups) or "query"
    if measures and groups:
        head = f"{head} by {_join(groups)}"
    head = head[:1].upper() + head[1:]

    if filters := query.get("filters"):
        notes.append(f"{len(filters)} filter" + ("s" if len(filters) > 1 else ""))
    if segments := query.get("segments"):
        notes.append(_join([_title(member, catalog) for member in segments]))
    if limit := query.get("limit"):
        notes.append(f"top {limit}" if query.get("order") else f"limit {limit}")

    return f"{head} · {', '.join(notes)}" if notes else head
