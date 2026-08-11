"""One-line human labels for Cube queries, derived from the query rather than asked of the
model — so a label cannot describe a query that was never run.
"""

from .catalog import Catalog


def _title(member_id: str, catalog: Catalog | None) -> str:
    """Cube's own short title when it has one, else the bare column name."""
    doc = catalog.get(member_id) if catalog else None
    if doc and doc.title:
        return doc.title.lower()
    return member_id.split(".")[-1].replace("_", " ")


def _join(parts: list[str]) -> str:
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


def describe_query(query: dict, catalog: Catalog | None = None) -> str:
    measures = [_title(m, catalog) for m in query.get("measures") or []]
    groups = [_title(d, catalog) for d in query.get("dimensions") or []]
    notes: list[str] = []

    for td in query.get("timeDimensions") or []:
        # A granularity groups the result; a bare dateRange only narrows it.
        if td.get("granularity"):
            groups.append(str(td["granularity"]))
        # Cube accepts either a named range ("last quarter") or a [from, to] pair.
        if value := td.get("dateRange"):
            notes.append(f"{value[0]} to {value[1]}" if isinstance(value, list) else str(value))

    head = _join(measures) or _join(groups) or "query"
    if measures and groups:
        head = f"{head} by {_join(groups)}"
    head = head[:1].upper() + head[1:]

    if filters := query.get("filters"):
        notes.append(f"{len(filters)} filter" + ("s" if len(filters) > 1 else ""))
    if segments := query.get("segments"):
        notes.append(_join([_title(s, catalog) for s in segments]))
    if limit := query.get("limit"):
        # An ordered limit is a top-N; an unordered one is just a cap.
        notes.append(f"top {limit}" if query.get("order") else f"limit {limit}")

    return f"{head} · {', '.join(notes)}" if notes else head
