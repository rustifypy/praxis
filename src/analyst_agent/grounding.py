"""Git-backed grounding artefacts: rules, skills, certified queries.

Markdown with YAML-ish frontmatter, mirroring Cube's agents/ convention.
Only merged files load, so every change to agent behaviour is a reviewed diff.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Artefact:
    name: str
    meta: dict
    body: str

    @property
    def description(self) -> str:
        return self.meta.get("description", "")


def _load_dir(root: Path, name: str) -> list[Artefact]:
    """Every .md in root/name, its frontmatter split off into meta."""
    directory = root / name
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text()
        meta: dict = {}
        body = text
        if text.startswith("---"):
            _, raw, body = text.split("---", 2)
            for line in raw.strip().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip().strip("\"'")
        out.append(Artefact(name=meta.get("name") or path.stem, meta=meta, body=body.strip()))
    return out


class Grounding:
    def __init__(self, root: str | Path):
        root = Path(root)
        self.rules = _load_dir(root, "rules")
        self.skills = _load_dir(root, "skills")
        self.certified = _load_dir(root, "certified_queries")

    @property
    def always_rules(self) -> str:
        picked = [a for a in self.rules if a.meta.get("type", "always") == "always"]
        return "\n\n".join(f"### {a.name}\n{a.body}" for a in picked)

    def catalog_lines(self) -> str:
        """Progressive disclosure: names and descriptions only."""
        items = [f"- skill `{a.name}`: {a.description}" for a in self.skills]
        items += [
            f"- rule `{a.name}`: {a.description}"
            for a in self.rules
            if a.meta.get("type") == "agent_requested"
        ]
        return "\n".join(items)

    def certified_for(self, question: str, limit: int = 3) -> list[Artefact]:
        from rapidfuzz import fuzz

        def target(a: Artefact) -> str:
            return f"{a.meta.get('user_request', '')} {a.description}".lower()

        scored = [(fuzz.token_set_ratio(question.lower(), target(a)), a) for a in self.certified]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [a for score, a in scored[:limit] if score > 45]

    def render_certified(self, question: str) -> str:
        picked = self.certified_for(question)
        if not picked:
            return ""
        blocks = []
        for a in picked:
            # The certified query is the artefact's fenced JSON block, reformatted.
            body = a.body
            if "```" in body:
                body = body.split("```")[1].removeprefix("json").strip()
            try:
                query = json.dumps(json.loads(body), indent=2)
            except Exception:
                query = body.strip()
            blocks.append(
                f"### {a.name}\nMatches requests like: {a.meta.get('user_request', '-')}\n"
                f"```json\n{query}\n```"
            )
        return "\n\n".join(blocks)
