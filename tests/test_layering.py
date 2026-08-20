"""The import graph, asserted.

An upward import is invisible until something breaks: one shared helper in the wrong package
is enough to make two features depend on each other, and an annotation hidden behind
`TYPE_CHECKING` is a cycle that simply has not failed yet.

Nothing here reads a docstring or a naming convention. It parses the imports.
"""

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "src" / "analyst_agent"

TIERS: dict[str, int] = {
    # 0 — settings, the database file, the event format, the chat model, the job runner,
    # the subprocess wrapper. Nothing above it may be named here.
    "core": 0,
    # 1 — the transcript, and one client per service the deployment runs: Cube, the model
    # store, the sandbox. None of the four knows the others exist.
    "cube": 1,
    "modelstore": 1,
    "sandbox": 1,
    "transcript": 1,
    # 2 — saved queries.
    "dashboards": 2,
    # 3 — the data model: the connection, what was generated, its branches. Reads dashboards
    # to answer "what would this edit break".
    "datamodel": 3,
    # 4 — everything the LLM touches: prompts, the three toolsets, one turn.
    "agent": 4,
    # 5 — HTTP. May reach anything; nothing may reach it.
    "web": 5,
    "__main__": 6,
}


def _packages() -> dict[str, set[str]]:
    """Top-level package -> the top-level packages it imports, resolved from relative
    imports only. Absolute imports of this package do not occur and would not be found."""
    edges: dict[str, set[str]] = {name: set() for name in TIERS}
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        owner = parts[0] if len(parts) > 1 else path.stem
        if owner == "__init__":
            continue
        here = list(parts[:-1])
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            # `level` 1 is this module's own directory, 2 its parent, and so on.
            base = here[: len(here) - (node.level - 1)]
            target = ".".join([*base, node.module or ""]).strip(".")
            if target:
                edges.setdefault(owner, set()).add(target.split(".")[0])
    for name, seen in edges.items():
        seen.discard(name)
    return edges


@pytest.mark.parametrize("package", sorted(TIERS))
def test_a_package_imports_only_its_own_tier_or_below(package):
    """The whole rule. A violation is not a style problem — it is a cycle waiting for the
    next person who imports the other direction."""
    edges = _packages()
    above = {other for other in edges[package] if TIERS[other] > TIERS[package]}
    assert not above, (
        f"{package} (tier {TIERS[package]}) imports "
        + ", ".join(f"{o} (tier {TIERS[o]})" for o in sorted(above))
        + " — move what it needs down, or pass it in as an argument"
    )


def test_core_names_nothing_outside_itself():
    """Stated separately from the tier rule because it is the one that keeps `core` from
    becoming a junk drawer: a module belongs there only if it needs nothing from above."""
    assert _packages()["core"] == set()


def test_every_package_is_placed():
    """A new top-level package has to choose a tier rather than silently escape the rule."""
    found = {
        p.parts[0] if len(p.parts) > 1 else p.stem
        for p in (q.relative_to(ROOT) for q in ROOT.rglob("*.py"))
    } - {"__init__"}
    assert found <= set(TIERS), f"unplaced: {sorted(found - set(TIERS))}"
