"""Grounding lives beside the cubes it names, so most of what can go wrong is path
resolution. None of it needs a container or a model: a directory and some markdown.
"""

import pytest

from analyst_agent.sources import grounding as loader
from analyst_agent.sources import registry
from analyst_agent.sources.grounding import Grounding
from analyst_agent.sources.registry import ARTEFACT_DIRS

RULE = """---
name: metric-definitions
type: always
description: Canonical definitions.
---

"Revenue" means `revenue.net_revenue`.
"""

ASKED_FOR = """---
name: period-comparison
type: agent_requested
description: How to compare two periods.
---

Run one query per period.
"""

SKILL = """---
name: channel-mix
description: Use when asked which channels drive revenue.
---

Group by `revenue.channel`.
"""

CERTIFIED = """---
name: monthly-net-revenue
user_request: monthly revenue trend, revenue by month
description: Net revenue by month.
---

```json
{"measures": ["revenue.net_revenue"]}
```
"""


@pytest.fixture
def grounded(tmp_path):
    """A populated grounding directory, one artefact of each kind."""
    for artefact in ARTEFACT_DIRS:
        (tmp_path / artefact).mkdir()
    (tmp_path / "rules" / "metric-definitions.md").write_text(RULE)
    (tmp_path / "rules" / "period-comparison.md").write_text(ASKED_FOR)
    (tmp_path / "skills" / "channel-mix.md").write_text(SKILL)
    (tmp_path / "certified_queries" / "monthly.md").write_text(CERTIFIED)
    return Grounding(tmp_path)


# --- loading ----------------------------------------------------------------


def test_frontmatter_splits_off_the_body(grounded):
    rule = next(r for r in grounded.rules if r.name == "metric-definitions")
    assert rule.meta["type"] == "always"
    assert rule.description == "Canonical definitions."
    assert rule.body.startswith('"Revenue" means')
    assert "---" not in rule.body


def test_only_always_rules_reach_the_system_prompt(grounded):
    assert "metric-definitions" in grounded.always_rules
    # agent_requested is offered by name in the catalog and loaded on demand, so
    # putting its body in every prompt would defeat the whole arrangement.
    assert "period-comparison" not in grounded.always_rules
    assert "period-comparison" in grounded.catalog_lines()
    assert "channel-mix" in grounded.catalog_lines()


def test_certified_queries_match_on_the_request_they_were_written_for(grounded):
    assert grounded.certified_for("show me the monthly revenue trend")
    assert not grounded.certified_for("which employee has the longest tenure")


def test_yaml_in_the_grounding_directory_is_never_loaded(tmp_path):
    """The directory sits inside Cube's bind-mounted model tree, and Cube compiles any
    `*.yml` under it — one there takes the whole model to zero cubes. Nothing should
    ever make writing one look useful."""
    (tmp_path / "certified_queries").mkdir()
    (tmp_path / "certified_queries" / "monthly.yml").write_text("name: monthly\nquery: {}\n")
    assert Grounding(tmp_path).certified == []


# --- the absence of grounding ------------------------------------------------


def test_a_freshly_scaffolded_model_is_empty(tmp_path):
    registry.scaffold_dirs(root=tmp_path)
    assert Grounding(registry.grounding_dir(tmp_path)).is_empty


def test_a_missing_directory_is_empty_rather_than_an_error(tmp_path):
    assert Grounding(tmp_path / "never-created").is_empty


def test_populated_grounding_is_not_empty(grounded):
    assert not grounded.is_empty


# --- what scaffolding leaves behind -----------------------------------------


def test_scaffolding_creates_every_directory_grounding_reads(tmp_path):
    """The end-to-end pin: an artefact dropped into each scaffolded directory has to
    come back out. Scaffolding a set the loader does not read would create folders
    that silently swallow rules."""
    registry.scaffold_dirs(root=tmp_path)
    root = registry.grounding_dir(tmp_path)
    for artefact in ARTEFACT_DIRS:
        (root / artefact / "x.md").write_text(f"---\nname: {artefact}\n---\n\nbody\n")

    grounding = Grounding(root)
    found = {a.name for a in grounding.rules + grounding.skills + grounding.certified}
    assert found == set(ARTEFACT_DIRS)


def test_grounding_lives_inside_the_directory_cube_mounts(tmp_path):
    """Not an accident worth losing: `publish.write` unlinks `*.yml` under this same
    tree on every `cubes` run, and leaves the markdown alone."""
    assert registry.grounding_dir(tmp_path).parent == registry.model_dir(tmp_path)


def test_load_reads_the_directory_beside_the_model(tmp_path, monkeypatch):
    """The one agent input resolved from the working directory rather than over HTTP, so
    a portal launched elsewhere grounds on nothing rather than erroring."""
    monkeypatch.setattr(registry, "project_root", lambda: tmp_path)
    registry.scaffold_dirs(root=tmp_path)
    assert loader.load().is_empty

    (registry.grounding_dir(tmp_path) / "rules" / "metrics.md").write_text(RULE)
    assert not loader.load().is_empty
