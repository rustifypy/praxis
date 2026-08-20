"""The grounding — rules, skills, certified queries — as markdown with frontmatter, read from
the model store beside the cubes and views it names.
"""

import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..core.deployment import MAIN
from ..modelstore import model_files
from .registry import GROUNDING_ROOT


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


def _load_dir(files: dict[str, str], name: str) -> list[Artefact]:
    """Every `grounding/<name>/*.md` in the file set, its frontmatter split off into meta."""
    prefix = f"{GROUNDING_ROOT}/{name}/"
    out = []
    for path, text in sorted(files.items()):
        if not path.startswith(prefix) or not path.endswith(".md"):
            continue
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
                name=meta.get("name") or PurePosixPath(path).stem,
                meta=meta,
                body=body.strip(),
                file=path.removeprefix(f"{GROUNDING_ROOT}/"),
            )
        )
    return out


class Grounding:
    """What the model's `grounding/` holds, or nothing at all — a model with none is a fresh
    install rather than an error.
    """

    def __init__(self, files: dict[str, str]):
        self.rules = _load_dir(files, "rules")
        self.skills = _load_dir(files, "skills")
        self.certified = _load_dir(files, "certified_queries")

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


async def load(ref: str = MAIN) -> Grounding:
    """The grounding at `ref`, re-read per turn so editing a rule takes effect on the next
    message.

    No store and no such ref both ground on nothing, which is what `model_files` decides for
    every reader of the model — and a store that does not answer raises there, because an agent
    quietly dropping the rules it is bound by is worse than a turn that says so.
    """
    return Grounding(await model_files(ref))
