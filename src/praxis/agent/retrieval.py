from rapidfuzz import fuzz

from ..modelstore.catalog import Catalog, Entity


class LexicalRetriever:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def search(self, question: str, limit: int = 3) -> list[Entity]:
        # Views first: they are the governed consumption surface. A cube whose every member a view
        # re-exports stays behind it — but a member no view carries can only be found on its
        # cube, so that cube is searched too. With any view present, such members were simply
        # never returned. Seen on a Flow whose transfer routes were a cube no view covered: the
        # agent searched eight times for them, then typed the routes CSV itself.
        uncovered = [
            cube
            for cube in self.catalog.cubes
            # Segments aside: a view does not re-export them, and a cube's filter preset is not
            # a column anyone is looking for.
            if any(
                member.id not in self.catalog.exposure
                for member in cube.members
                if member.kind != "segment"
            )
        ]
        pool = [*self.catalog.views, *uncovered] or self.catalog.entities
        scored = [
            (fuzz.token_set_ratio(question.lower(), entity.search_text.lower()), entity)
            for entity in pool
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entity for _, entity in scored[:limit]]


def render(entities: list[Entity]) -> str:
    blocks = []
    for entity in entities:
        head = f"## {entity.name} ({'view' if entity.is_view else 'cube'})"
        if entity.description:
            head += f"\n{entity.description}"
        if entity.ai_context:
            head += f"\nAgent note: {entity.ai_context}"
        lines = [
            head,
            "",
            "| member | kind | type | notes | valid operators |",
            "|---|---|---|---|---|",
        ]
        for member in entity.members:
            notes = " ".join(filter(None, [member.description, member.ai_context]))
            if member.granularities:
                notes += f" (granularities: {', '.join(member.granularities)})"
            lines.append(
                f"| {member.id} | {member.kind} | {member.type} | {notes.strip() or '-'} | "
                f"{', '.join(member.valid_operators) or '-'} |"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
