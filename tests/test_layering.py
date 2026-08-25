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

# Tier 1 is external integrations, and there are exactly two. Everything process-local —
# settings, the sqlite file and every table's accessors, the event format, the LLM builder, the
# job runner, the deployment row and its hand-over — is tier 0.
TIERS: dict[str, int] = {
    # 0 — nothing above it may be named here. `llm.py` is an integration too and stays anyway:
    # it is a builder rather than a client, and every tier needs it.
    "core": 0,
    # 1 — one package per service the deployment runs. Neither knows the other exists, which is
    # why each holds its own reachability: the question is asked about an address that is not on
    # the row yet, and about a service that may not be there.
    "modelstore": 1,
    "sandbox": 1,
    # 2 — everything the LLM touches: prompts, the toolsets, one turn.
    "agent": 2,
    # 3 — HTTP. May reach anything; nothing may reach it.
    "api": 3,
    "__main__": 4,
}

# Rows the model store must never read. `impact` is handed the saved queries it checks against
# rather than looking them up — the edge this arrangement exists to cut, and the one that gets
# re-added by accident the first time someone needs a tile inside a verdict.
FORBIDDEN = {"modelstore": {"core.boards"}}


def _edges() -> dict[str, set[str]]:
    """Top-level package -> every module it imports, dotted and resolved from relative imports
    only. Absolute imports of this package do not occur and would not be found."""
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
            if not target:
                continue
            seen = edges.setdefault(owner, set())
            seen.add(target)
            # `from ...core import boards` names a module too, and recording only `core` is
            # how a forbidden edge hides: the two spellings have to read the same here.
            seen.update(f"{target}.{alias.name}" for alias in node.names)
    return edges


def _packages() -> dict[str, set[str]]:
    """The same graph, collapsed to top-level package names — what the tier rule is about."""
    collapsed = {owner: {t.split(".")[0] for t in seen} for owner, seen in _edges().items()}
    for name, seen in collapsed.items():
        seen.discard(name)
    return collapsed


@pytest.mark.parametrize("package", sorted(TIERS))
def test_a_package_imports_only_its_own_tier_or_below(package):
    """The whole rule. A violation is not a style problem — it is a cycle waiting for the
    next person who imports the other direction."""
    edges = _packages()
    # Unplaced packages are `test_every_package_is_placed`'s complaint. Skipping them here keeps
    # this failure readable while a refactor is mid-flight, instead of a KeyError naming one.
    above = {o for o in edges[package] if TIERS.get(o, -1) > TIERS[package]}
    assert not above, (
        f"{package} (tier {TIERS[package]}) imports "
        + ", ".join(f"{o} (tier {TIERS[o]})" for o in sorted(above))
        + " — move what it needs down, or pass it in as an argument"
    )


def test_core_names_nothing_outside_itself():
    """Stated separately from the tier rule because it is the one that keeps `core` from
    becoming a junk drawer: a module belongs there only if it needs nothing from above."""
    assert _packages()["core"] == set()


def test_the_two_integrations_do_not_know_each_other():
    """Same tier, so the rule above permits the edge and this is the only thing refusing it.
    One deployment runs both, and the moment either can reach the other, "which service is
    down" stops being answerable from one place."""
    edges = _packages()
    assert "sandbox" not in edges["modelstore"]
    assert "modelstore" not in edges["sandbox"]


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_a_package_does_not_name_what_it_is_kept_from(package):
    """A tier is too coarse for some edges: `core` is below everything, so the tier rule cannot
    say that one thing in it is off limits to one package above."""
    named = {t for t in _edges()[package] if any(t.startswith(f) for f in FORBIDDEN[package])}
    assert not named, (
        f"{package} names {', '.join(sorted(named))} — pass what it needs in as an argument"
    )


def test_every_package_is_placed():
    """A new top-level package has to choose a tier rather than silently escape the rule."""
    found = {
        p.parts[0] if len(p.parts) > 1 else p.stem
        for p in (q.relative_to(ROOT) for q in ROOT.rglob("*.py"))
    } - {"__init__"}
    assert found <= set(TIERS), f"unplaced: {sorted(found - set(TIERS))}"
