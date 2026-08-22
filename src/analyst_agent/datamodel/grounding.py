import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from rapidfuzz import fuzz

from ..core.deployment import MAIN
from ..modelstore import model_files
from .registry import GROUNDING_ROOT


@dataclass
class Artefact:
    name: str
    meta: dict
    body: str
    # Frontmatter `name` falls back to the stem but need not match it, so this path is the only
    # thing that says which file to edit.
    file: str = ""

    @property
    def description(self) -> str:
        return self.meta.get("description", "")

    @property
    def fenced(self) -> str:
        if "```" not in self.body:
            return self.body.strip()
        return self.body.split("```")[1].removeprefix("json").strip()

    @property
    def query(self) -> dict | None:
        try:
            parsed = json.loads(self.fenced)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None


def _load_dir(files: dict[str, str], name: str) -> list[Artefact]:
    prefix = f"{GROUNDING_ROOT}/{name}/"
    artefacts = []
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
        artefacts.append(
            Artefact(
                name=meta.get("name") or PurePosixPath(path).stem,
                meta=meta,
                body=body.strip(),
                file=path.removeprefix(f"{GROUNDING_ROOT}/"),
            )
        )
    return artefacts


class Grounding:
    def __init__(self, files: dict[str, str]):
        self.rules = _load_dir(files, "rules")
        self.skills = _load_dir(files, "skills")
        self.certified = _load_dir(files, "certified_queries")

    @property
    def is_empty(self) -> bool:
        return not (self.rules or self.skills or self.certified)

    @property
    def always_rules(self) -> str:
        picked = [
            artefact for artefact in self.rules if artefact.meta.get("type", "always") == "always"
        ]
        return "\n\n".join(f"### {artefact.name}\n{artefact.body}" for artefact in picked)

    def catalog_lines(self) -> str:
        # Progressive disclosure: names and descriptions only.
        items = [f"- skill `{artefact.name}`: {artefact.description}" for artefact in self.skills]
        items += [
            f"- rule `{artefact.name}`: {artefact.description}"
            for artefact in self.rules
            if artefact.meta.get("type") == "agent_requested"
        ]
        return "\n".join(items)

    def certified_for(self, question: str, limit: int = 3) -> list[Artefact]:
        def match_text(artefact: Artefact) -> str:
            return f"{artefact.meta.get('user_request', '')} {artefact.description}".lower()

        scored = [
            (fuzz.token_set_ratio(question.lower(), match_text(artefact)), artefact)
            for artefact in self.certified
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [artefact for score, artefact in scored[:limit] if score > 45]

    def render_certified(self, question: str) -> str:
        picked = self.certified_for(question)
        if not picked:
            return ""
        blocks = []
        for artefact in picked:
            # Reformatted when it parses, verbatim when it does not: a query that is not JSON
            # is still worth showing the agent.
            parsed = artefact.query
            query = json.dumps(parsed, indent=2) if parsed is not None else artefact.fenced
            blocks.append(
                f"### {artefact.name}\nMatches requests like: "
                f"{artefact.meta.get('user_request', '-')}\n"
                f"```json\n{query}\n```"
            )
        return "\n\n".join(blocks)


async def load(ref: str = MAIN) -> Grounding:
    # Re-read per turn, so editing a rule takes effect on the next message.
    return Grounding(await model_files(ref))
