"""What merging a branch would break — the members saved queries name, against what this
source is serving now."""

from dataclasses import dataclass, field

from pydantic import ValidationError

from ...cube.catalog import Catalog
from ...cube.schema import CubeQuery
from ...dashboards.store import DashboardStore
from ...grounding import Grounding


@dataclass
class Broken:
    kind: str
    """`tile` or `certified`."""
    where: str
    """The dashboard it sits on, or the grounding file it lives in."""
    title: str
    members: list[str]
    nearest: dict[str, list[str]] = field(default_factory=dict)
    """Closest surviving names, so a fix is one edit rather than a search."""


@dataclass
class Impact:
    checked: int = 0
    broken: list[Broken] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.broken

    def as_message(self) -> str:
        if self.ok:
            return ""
        lines = []
        for item in self.broken:
            for member in item.members:
                hint = ", ".join(item.nearest.get(member) or [])
                lines.append(
                    f"- {item.kind} {item.title!r} ({item.where}) reads {member}, which this "
                    f"model no longer serves{f' — closest: {hint}' if hint else ''}"
                )
        return f"{len(self.broken)} saved queries would break:\n" + "\n".join(lines)


def missing_members(query: dict, catalog: Catalog) -> tuple[list[str], dict[str, list[str]]]:
    """Members this query names that the catalog does not have, and what is nearest to each."""
    try:
        parsed = CubeQuery.model_validate(query)
    except ValidationError:
        # A stored query that no longer parses is not this check's problem — the gate that
        # runs before every refresh reports it, with the schema errors attached.
        return [], {}
    absent = sorted({m for m in parsed.referenced_members() if catalog.get(m) is None})
    return absent, {m: catalog.nearest(m) for m in absent}


async def check(
    source_name: str,
    catalog: Catalog,
    dashboards: DashboardStore,
    grounding: Grounding,
) -> Impact:
    """Every saved query bound to this source, against the model it is serving.

    Deliberately not a diff of main's members against the branch's: only one catalog is ever
    served at a time, and asking "is what this tile reads still here" answers the question that
    matters without checking main out to find the answer. It also catches breakage whatever
    caused it — a renamed view, a changed alias, an include quietly dropped.
    """
    out = Impact()
    for board in await dashboards.list_dashboards():
        if board.source_name != source_name:
            continue
        for tile in await dashboards.tiles(board.id):
            out.checked += 1
            absent, nearest = missing_members(tile.query, catalog)
            if absent:
                out.broken.append(Broken("tile", board.title, tile.title, absent, nearest))

    for artefact in grounding.certified:
        query = artefact.query
        if query is None:
            continue
        out.checked += 1
        absent, nearest = missing_members(query, catalog)
        if absent:
            out.broken.append(Broken("certified", artefact.file, artefact.name, absent, nearest))
    return out
