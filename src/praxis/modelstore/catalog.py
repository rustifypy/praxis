from dataclasses import dataclass, field

from rapidfuzz import fuzz

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
    # Accepted on a timestamp despite Cube documenting them for numbers only.
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
    format: str = ""
    alias_member: str = ""

    @property
    def search_text(self) -> str:
        return " ".join(filter(None, [self.id, self.title, self.description, self.ai_context]))


@dataclass
class Folder:
    name: str
    members: list[str] = field(default_factory=list)


@dataclass
class Entity:
    name: str
    is_view: bool
    title: str = ""
    description: str = ""
    ai_context: str = ""
    members: list[MemberDoc] = field(default_factory=list)
    folders: list[Folder] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        head = " ".join(filter(None, [self.name, self.title, self.description, self.ai_context]))
        return head + " " + " ".join(member.search_text for member in self.members)


@dataclass
class Counts:
    cubes: int
    views: int
    members: int
    exposed: int
    described: int


class Catalog:
    def __init__(self, entities: list[Entity]):
        self.entities = entities
        self.members: dict[str, MemberDoc] = {
            member.id: member for entity in entities for member in entity.members
        }
        self.views = [entity for entity in entities if entity.is_view]
        self.cubes = [entity for entity in entities if not entity.is_view]
        # Which views re-export each cube member, keyed by the aliased cube member.
        self.exposure: dict[str, list[str]] = {}
        for view in self.views:
            for member in view.members:
                if member.alias_member:
                    self.exposure.setdefault(member.alias_member, []).append(view.name)

    def draws_from(self, entity: Entity) -> list[str]:
        if not entity.is_view:
            return []
        return sorted(
            {
                member.alias_member.rpartition(".")[0]
                for member in entity.members
                if member.alias_member
            }
        )

    def exposed_by(self, entity: Entity) -> list[str]:
        if entity.is_view:
            return []
        return sorted(
            {view for member in entity.members for view in self.exposure.get(member.id, [])}
        )

    def counts(self) -> Counts:
        members = [member for entity in self.cubes for member in entity.members]
        return Counts(
            cubes=len(self.cubes),
            views=len(self.views),
            members=len(members),
            exposed=sum(1 for member in members if self.exposure.get(member.id)),
            described=sum(1 for member in members if member.description),
        )

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
                folders=[
                    Folder(name=folder.get("name", ""), members=list(folder.get("members") or []))
                    for folder in raw.get("folders") or []
                ],
            )
            for kind, key in (
                ("measure", "measures"),
                ("dimension", "dimensions"),
                ("segment", "segments"),
            ):
                for raw_member in raw.get(key) or []:
                    name = raw_member["name"]
                    member_id = name if "." in name else f"{entity.name}.{name}"
                    member_type = raw_member.get("type", "")
                    if kind == "measure":
                        operators = MEASURE_OPS
                    elif kind == "dimension":
                        operators = DIMENSION_OPS.get(member_type, STRING_OPS)
                    else:
                        operators = []
                    grains = [
                        grain["name"] if isinstance(grain, dict) else grain
                        for grain in raw_member.get("granularities") or []
                    ]
                    if kind == "dimension" and member_type == "time" and not grains:
                        grains = ["day", "week", "month", "quarter", "year"]
                    entity.members.append(
                        MemberDoc(
                            id=member_id,
                            kind=kind,
                            cube=entity.name,
                            type=member_type,
                            title=raw_member.get("shortTitle") or raw_member.get("title", ""),
                            description=raw_member.get("description", ""),
                            ai_context=_ai_context(raw_member),
                            granularities=grains,
                            valid_operators=list(operators),
                            format=raw_member.get("format", "") or "",
                            alias_member=raw_member.get("aliasMember", "") or "",
                        )
                    )
            entities.append(entity)
        return cls(entities)

    def get(self, member_id: str) -> MemberDoc | None:
        return self.members.get(member_id)

    def nearest(self, member_id: str, limit: int = 3) -> list[str]:
        # Scored on the part after the dot; scoring the whole id ranks by shared cube prefix.
        entity, _, wanted = member_id.rpartition(".")
        scored = []
        for candidate in self.members:
            candidate_entity, _, candidate_member = candidate.rpartition(".")
            # snake_case to spaces, so token-set scoring can see the words.
            score = fuzz.token_set_ratio(
                wanted.replace("_", " ").lower(), candidate_member.replace("_", " ").lower()
            )
            if entity and candidate_entity == entity:
                score += 10
            if score >= 60:
                scored.append((score, candidate))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [candidate for _, candidate in scored[:limit]]

    def views_containing(self, member_ids: list[str]) -> list[str]:
        wanted = set(member_ids)
        return [
            view.name for view in self.views if wanted <= {member.id for member in view.members}
        ]


def _ai_context(raw: dict) -> str:
    return (raw.get("meta") or {}).get("ai_context", "") or ""
