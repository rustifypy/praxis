from dataclasses import dataclass, field

from pydantic import ValidationError

from ..catalog import Catalog
from ..schema import CubeQuery


@dataclass
class Broken:
    # `tile`, and the dashboard it sits on.
    kind: str
    where: str
    title: str
    members: list[str]
    # Closest surviving names, so a fix is one edit rather than a search.
    nearest: dict[str, list[str]] = field(default_factory=dict)


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
    try:
        parsed = CubeQuery.model_validate(query)
        absent = sorted(
            {member for member in parsed.referenced_members() if catalog.get(member) is None}
        )
        return absent, {member: catalog.nearest(member) for member in absent}
    except ValidationError:
        # A stored query that no longer parses is not this check's problem — the gate that runs
        # before every refresh reports it, with the schema errors attached.
        return [], {}


def check(catalog: Catalog, saved: list[dict]) -> Impact:
    """Which of `saved` reads a member this catalog no longer serves.

    The queries are passed in rather than read, and that is the whole arrangement: they live in
    `core.boards`, and a model store that reached for them would make dashboards a dependency of
    the semantic layer. `test_layering.FORBIDDEN` is what keeps it that way. Each entry is
    `{kind, where, title, query}` — see `core.boards.saved_queries`.

    Deliberately not a diff of main's members against the ref's: "is what this tile reads still
    here" catches breakage whatever caused it — a renamed view, a changed alias, an include
    quietly dropped.
    """
    impact = Impact(checked=len(saved))
    for entry in saved:
        absent, nearest = missing_members(entry["query"], catalog)
        if absent:
            impact.broken.append(
                Broken(entry["kind"], entry["where"], entry["title"], absent, nearest)
            )
    return impact
