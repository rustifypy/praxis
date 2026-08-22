from rapidfuzz import fuzz

from ..cube.catalog import Catalog, Entity


class LexicalRetriever:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def search(self, question: str, limit: int = 3) -> list[Entity]:
        # Views first: they are the governed consumption surface.
        pool = self.catalog.views or self.catalog.entities
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
