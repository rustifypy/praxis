"""The grounding — rules, skills, certified queries — as markdown with frontmatter, read
from the model directory beside the cubes and views it names.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from . import registry


@dataclass
class Artefact:
    name: str
    meta: dict
    body: str
    file: str = ""
    """Path under the grounding directory. Frontmatter `name` falls back to the stem but
    need not match it, so this is the only thing that says which file to edit."""

    @property
    def description(self) -> str:
        return self.meta.get("description", "")

    @property
    def fenced(self) -> str:
        """The artefact's fenced code block, or its whole body when it has none."""
        if "```" not in self.body:
            return self.body.strip()
        return self.body.split("```")[1].removeprefix("json").strip()

    @property
    def query(self) -> dict | None:
        """A certified query as Cube JSON, or None when it does not parse."""
        try:
            parsed = json.loads(self.fenced)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None


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
        out.append(
            Artefact(
                name=meta.get("name") or path.stem,
                meta=meta,
                body=body.strip(),
                file=f"{name}/{path.name}",
            )
        )
    return out


class Grounding:
    """What the grounding directory holds, or nothing at all — a directory that does not
    exist yet is a fresh install rather than an error.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.rules = _load_dir(self.root, "rules")
        self.skills = _load_dir(self.root, "skills")
        self.certified = _load_dir(self.root, "certified_queries")

    @property
    def is_empty(self) -> bool:
        """No artefacts at all — the agent is answering on the system prompt alone."""
        return not (self.rules or self.skills or self.certified)

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
            # Reformatted when it parses, verbatim when it does not — a query that is not
            # JSON is still worth showing the agent.
            parsed = a.query
            query = json.dumps(parsed, indent=2) if parsed is not None else a.fenced
            blocks.append(
                f"### {a.name}\nMatches requests like: {a.meta.get('user_request', '-')}\n"
                f"```json\n{query}\n```"
            )
        return "\n\n".join(blocks)


def load() -> Grounding:
    """The grounding as the agent reads it, re-read per turn so editing a rule takes effect on
    the next message.

    Resolved from the working directory (`registry.project_root`), which the model directory
    also is — launch the portal from elsewhere and both resolve to a directory that is not
    there, grounding silently and `publish.write` loudly.
    """
    return Grounding(registry.grounding_dir())
