"""One source's grounding artefacts: rules, skills, certified queries.

Markdown with YAML-ish frontmatter, mirroring Cube's agents/ convention.
Only merged files load, so every change to agent behaviour is a reviewed diff.

Grounding belongs to a data source rather than to the installation. It names members —
"revenue" means `revenue.net_revenue`, this skill groups by `revenue.channel` — so a
single global set is wrong the moment there are two warehouses, and was being fed to
every source regardless of whether those members existed. `for_active_source` is the
only way the agent gets one, and there is no global layer behind it: a source's
grounding is what it carries, or nothing.

Markdown only, and not by preference. The directory is inside Cube's bind-mounted model
tree, and Cube compiles any `*.yml` under it — see `registry.GROUNDING_ROOT`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .sources.registry import Source
from .sources.store import SourceStore


@dataclass
class Artefact:
    name: str
    meta: dict
    body: str
    file: str = ""
    """The filename on disk. Frontmatter `name` falls back to the stem but does not have
    to match it, so the panel cannot tell you which file to edit without this."""

    @property
    def description(self) -> str:
        return self.meta.get("description", "")


def _load_dir(root: Path | None, name: str) -> list[Artefact]:
    """Every .md in root/name, its frontmatter split off into meta."""
    if root is None:
        return []
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
    """What one source's grounding directory holds, or nothing at all.

    `root=None` is the real state a source can be in, not a test convenience: a fresh
    source has empty directories, and a `CUBE_URL` pointing outside the registry has no
    directory at all. `source` records which one this came from so a caller can say so.
    """

    def __init__(self, root: str | Path | None = None, source: str = ""):
        self.root = Path(root) if root is not None else None
        self.source = source
        self.rules = _load_dir(self.root, "rules")
        self.skills = _load_dir(self.root, "skills")
        self.certified = _load_dir(self.root, "certified_queries")

    @property
    def is_empty(self) -> bool:
        """No artefacts at all — the agent is answering on the system prompt alone.

        Worth asking before an answer is trusted: nothing about a missing rule is
        visible in the answer it changes.
        """
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


def for_source(source: Source | None) -> Grounding:
    """The grounding a source carries. `None` — no source — carries none."""
    if source is None:
        return Grounding()
    return Grounding(source.grounding_dir(), source=source.name)


async def for_active_source() -> Grounding:
    """The grounding for whichever source `CUBE_URL` points at.

    Opens the registry itself rather than taking a store. Both ways into the agent need
    this — the browser's streamed turn and `/ask` — and threading a store through two
    signatures is how the two would come to resolve grounding differently.

    Read per turn, not cached: `Grounding` re-reads the directory each time, which is
    what makes editing a rule take effect on the next message. Cube's model needs a
    container restart for the same edit; the asymmetry is deliberate but surprising.

    A `CUBE_URL` with no source row behind it — a hand-run container, Cube Cloud, a port
    typo — yields empty grounding rather than an error. The agent still answers; it
    answers without the business's definitions, which is why `is_empty` exists.

    TODO: anchor this path absolutely, or serve it over the API. It is the only one of
    the agent's three inputs that resolves from the working directory — the model comes
    over HTTP, `sources.db` is an absolute path, and grounding walks up from `cwd` via
    `registry.project_root`. Launch the portal from anywhere but the project root and the
    model still answers while grounding silently resolves to a directory that does not
    exist. The container does not have this problem because its mount was baked in at
    `connect`; grounding re-resolves every turn. Storing the source's directory in its
    `sources.db` row fixes this and the same latent bug in `publish.write`, which would
    otherwise rebuild into a directory nothing is mounted on.

    Serving it over HTTP is the other option but a worse one on its own: the agent runs
    inside the portal process, so that is a network hop to read a local file, and the
    per-turn re-read is what makes editing a rule take effect on the next message.
    """
    store = SourceStore()
    await store.init()
    return for_source(next((s for s in await store.all() if s.is_active), None))
