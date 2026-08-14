"""Grounding is per-source, so most of what can go wrong is path resolution.

None of it needs a container or a model: a directory, some markdown, and the rule for
picking the source `CUBE_URL` names.
"""

import pytest

from analyst_agent.core.config import settings
from analyst_agent.sources import registry
from analyst_agent.sources.grounding import Grounding, for_active_source, for_source
from analyst_agent.sources.registry import ARTEFACT_DIRS, Source
from analyst_agent.sources.store import SourceStore

SHOP = Source(name="shop", description="Orders.", driver="postgres", port=4001)

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
    return Grounding(tmp_path, source="shop")


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


def test_no_source_carries_no_grounding():
    grounding = for_source(None)
    assert grounding.is_empty and grounding.source == ""


def test_a_source_with_nothing_written_yet_is_empty(tmp_path):
    registry.scaffold_source_dirs(SHOP, root=tmp_path)
    assert Grounding(SHOP.grounding_dir(tmp_path)).is_empty


def test_a_missing_directory_is_empty_rather_than_an_error(tmp_path):
    assert Grounding(tmp_path / "never-created").is_empty


def test_populated_grounding_is_not_empty(grounded):
    assert not grounded.is_empty


# --- what scaffolding leaves behind -----------------------------------------


def test_scaffolding_creates_every_directory_grounding_reads(tmp_path):
    """The end-to-end pin: an artefact dropped into each scaffolded directory has to
    come back out. Scaffolding a set the loader does not read would create folders
    that silently swallow rules."""
    registry.scaffold_source_dirs(SHOP, root=tmp_path)
    root = SHOP.grounding_dir(tmp_path)
    for artefact in ARTEFACT_DIRS:
        (root / artefact / "x.md").write_text(f"---\nname: {artefact}\n---\n\nbody\n")

    grounding = Grounding(root)
    found = {a.name for a in grounding.rules + grounding.skills + grounding.certified}
    assert found == set(ARTEFACT_DIRS)


def test_grounding_lives_inside_the_directory_cube_mounts(tmp_path):
    """Not an accident worth losing: `publish.write` unlinks `*.yml` under this same
    tree on every `cubes` run, and leaves the markdown alone."""
    assert SHOP.grounding_dir(tmp_path).parent == SHOP.model_dir(tmp_path)


# --- resolving through CUBE_URL ---------------------------------------------


@pytest.fixture
def registered(tmp_path, monkeypatch):
    """Two sources in a scratch registry, and `CUBE_URL` pointing at the second."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


async def test_active_source_decides_whose_grounding_loads(registered, monkeypatch):
    store = SourceStore()
    await store.init()
    await store.add(SHOP)
    await store.add(Source(name="ads", description="Spend.", driver="postgres", port=4002))

    monkeypatch.setattr(settings, "cube_url", "http://localhost:4002")
    assert (await for_active_source()).source == "ads"

    monkeypatch.setattr(settings, "cube_url", "http://localhost:4001")
    assert (await for_active_source()).source == "shop"


async def test_a_cube_url_outside_the_registry_grounds_on_nothing(registered, monkeypatch):
    """A hand-run container, Cube Cloud, or a port typo. The agent still answers — it
    answers without the business's definitions, and `is_empty` is how a caller says so."""
    store = SourceStore()
    await store.init()
    await store.add(SHOP)

    monkeypatch.setattr(settings, "cube_url", "http://localhost:9999")
    grounding = await for_active_source()
    assert grounding.source == "" and grounding.is_empty
