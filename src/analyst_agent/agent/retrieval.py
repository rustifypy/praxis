"""Semantic model retrieval, lexical (rapidfuzz) so the scaffold needs no vector store.
Swap in pgvector by replacing LexicalRetriever with the same `search(question, limit)`.
"""

from rapidfuzz import fuzz

from ..cube.catalog import Catalog, Entity


class LexicalRetriever:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def search(self, question: str, limit: int = 3) -> list[Entity]:
        # Views first: they are the governed consumption surface.
        pool = self.catalog.views or self.catalog.entities
        scored = [(fuzz.token_set_ratio(question.lower(), e.search_text.lower()), e) for e in pool]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [e for _, e in scored[:limit]]


def render(entities: list[Entity]) -> str:
    blocks = []
    for e in entities:
        head = f"## {e.name} ({'view' if e.is_view else 'cube'})"
        if e.description:
            head += f"\n{e.description}"
        if e.ai_context:
            head += f"\nAgent note: {e.ai_context}"
        lines = [
            head,
            "",
            "| member | kind | type | notes | valid operators |",
            "|---|---|---|---|---|",
        ]
        for m in e.members:
            notes = " ".join(filter(None, [m.description, m.ai_context]))
            if m.granularities:
                notes += f" (granularities: {', '.join(m.granularities)})"
            lines.append(
                f"| {m.id} | {m.kind} | {m.type} | {notes.strip() or '-'} | "
                f"{', '.join(m.valid_operators) or '-'} |"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
