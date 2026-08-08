from dataclasses import dataclass, field

from rapidfuzz import fuzz


def _tokens(name: str) -> str:
    """snake_case to space-separated, so token-set scoring can see the words."""
    return name.replace("_", " ").lower()


STRING_OPS = [
    "equals",
    "notEquals",
    "contains",
    "notContains",
    "startsWith",
    "notStartsWith",
    "endsWith",
    "notEndsWith",
    "set",
    "notSet",
]
NUMBER_OPS = ["equals", "notEquals", "gt", "gte", "lt", "lte", "set", "notSet"]
TIME_OPS = [
    "equals",
    "notEquals",
    "inDateRange",
    "notInDateRange",
    "beforeDate",
    "beforeOrOnDate",
    "afterDate",
    "afterOrOnDate",
    # Cube's docs list these under number dimensions only, but it accepts them on a
    # timestamp and answers correctly — verified against a live source. Omitting them
    # rejected a working query. `afterDate` and friends remain the idiomatic form.
    "gt",
    "gte",
    "lt",
    "lte",
    "set",
    "notSet",
]
BOOLEAN_OPS = ["equals", "notEquals", "set", "notSet"]
MEASURE_OPS = [*NUMBER_OPS, "measureFilter"]

DIMENSION_OPS = {
    "string": STRING_OPS,
    "number": NUMBER_OPS,
    "time": TIME_OPS,
    "boolean": BOOLEAN_OPS,
    "geo": STRING_OPS,
}


@dataclass
class MemberDoc:
    id: str
    kind: str  # measure | dimension | segment
    cube: str
    type: str
    title: str = ""
    description: str = ""
    ai_context: str = ""
    granularities: list[str] = field(default_factory=list)
    valid_operators: list[str] = field(default_factory=list)
    alias_member: str = ""
    """For a view member, the cube member it re-exports — `sales.revenue` ->
    `invoice_line.line_amount`. Empty on a cube's own members.

    This is the only thing in `/v1/meta` that says what a view is made of. Without it a
    view and a cube are two entities with different type strings, which is how they were
    being presented; with it, a view is a projection over named cubes and a cube member
    can be asked whether any view exposes it at all."""

    @property
    def search_text(self) -> str:
        return " ".join(filter(None, [self.id, self.title, self.description, self.ai_context]))


@dataclass
class Entity:
    """A cube or a view from /v1/meta."""

    name: str
    is_view: bool
    title: str = ""
    description: str = ""
    ai_context: str = ""
    members: list[MemberDoc] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        head = " ".join(filter(None, [self.name, self.title, self.description, self.ai_context]))
        return head + " " + " ".join(m.search_text for m in self.members)


class Catalog:
    def __init__(self, entities: list[Entity]):
        self.entities = entities
        self.members: dict[str, MemberDoc] = {m.id: m for e in entities for m in e.members}
        self.views = [e for e in entities if e.is_view]

    @classmethod
    def from_meta(cls, meta: dict) -> "Catalog":
        entities = []
        for raw in meta.get("cubes", []):
            entity = Entity(
                name=raw["name"],
                is_view=raw.get("type") == "view",
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                ai_context=_ai_context(raw),
            )
            for kind, key in (
                ("measure", "measures"),
                ("dimension", "dimensions"),
                ("segment", "segments"),
            ):
                for m in raw.get(key) or []:
                    member_id = m["name"] if "." in m["name"] else f"{entity.name}.{m['name']}"
                    mtype = m.get("type", "")
                    if kind == "measure":
                        ops = MEASURE_OPS
                    elif kind == "dimension":
                        ops = DIMENSION_OPS.get(mtype, STRING_OPS)
                    else:
                        ops = []
                    grains = [
                        g["name"] if isinstance(g, dict) else g
                        for g in m.get("granularities") or []
                    ]
                    if kind == "dimension" and mtype == "time" and not grains:
                        grains = ["day", "week", "month", "quarter", "year"]
                    entity.members.append(
                        MemberDoc(
                            id=member_id,
                            kind=kind,
                            cube=entity.name,
                            type=mtype,
                            title=m.get("shortTitle") or m.get("title", ""),
                            description=m.get("description", ""),
                            ai_context=_ai_context(m),
                            granularities=grains,
                            valid_operators=list(ops),
                            alias_member=m.get("aliasMember", "") or "",
                        )
                    )
            entities.append(entity)
        return cls(entities)

    def get(self, member_id: str) -> MemberDoc | None:
        return self.members.get(member_id)

    def nearest(self, member_id: str, limit: int = 3) -> list[str]:
        """Real members to suggest for one the agent invented.

        Scored on the part after the dot. Scoring the whole id ranks by the shared
        cube prefix instead — every member of `sales` scores alike, so `sales.revenue`
        came back with three arbitrary `sales.` members and not the one it meant.
        Underscores become spaces so `unit_price` and `price` share a token, and a
        member of the cube the agent already named wins a tie.

        Returning nothing is a correct answer: a name with no lexical neighbour is
        better reported as such than padded out with misleading suggestions.
        """
        entity, _, wanted = member_id.rpartition(".")
        scored = []
        for candidate in self.members:
            cand_entity, _, cand_member = candidate.rpartition(".")
            score = fuzz.token_set_ratio(_tokens(wanted), _tokens(cand_member))
            if entity and cand_entity == entity:
                score += 10
            if score >= 60:
                scored.append((score, candidate))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [candidate for _, candidate in scored[:limit]]

    def entity(self, name: str) -> Entity | None:
        return next((e for e in self.entities if e.name == name), None)

    def views_containing(self, member_ids: list[str]) -> list[str]:
        wanted = set(member_ids)
        return [v.name for v in self.views if wanted <= {m.id for m in v.members}]


def _ai_context(raw: dict) -> str:
    return (raw.get("meta") or {}).get("ai_context", "") or ""
