"""Grounding comes out of the model store beside the cubes it names, so what can go wrong is
path filtering and a ref that is not there. No store: respx answers.
"""

import httpx
import pytest
import respx

from analyst_agent.core.deployment import Deployment
from analyst_agent.datamodel import grounding as loader
from analyst_agent.datamodel.grounding import Grounding
from analyst_agent.datamodel.registry import ARTEFACT_DIRS, GROUNDING_ROOT
from analyst_agent.modelstore import StoreError

URL = "http://store:8500"

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

FILES = {
    "cubes/orders.yml": "cubes: []\n",
    "views/sales.yml": "views: []\n",
    "grounding/rules/metric-definitions.md": RULE,
    "grounding/rules/period-comparison.md": ASKED_FOR,
    "grounding/skills/channel-mix.md": SKILL,
    "grounding/certified_queries/monthly.md": CERTIFIED,
}


@pytest.fixture
def grounded() -> Grounding:
    """A whole model as the store hands it over, one artefact of each kind."""
    return Grounding(FILES)


@pytest.fixture
def configured(monkeypatch):
    """A deployment with a store, so `open_store` builds a client rather than refusing."""

    async def deployment():
        return Deployment(store_url=URL, store_token="writetok")

    monkeypatch.setattr("analyst_agent.modelstore.client.current", deployment)


# --- loading ----------------------------------------------------------------


def test_frontmatter_splits_off_the_body(grounded):
    rule = next(r for r in grounded.rules if r.name == "metric-definitions")
    assert rule.meta["type"] == "always"
    assert rule.description == "Canonical definitions."
    assert rule.body.startswith('"Revenue" means')
    assert "---" not in rule.body


def test_the_file_is_named_relative_to_the_grounding_directory(grounded):
    """What the panel offers to go and edit, so it is a path under `grounding/` rather than
    the store path or a bare name."""
    assert {a.file for a in grounded.skills} == {"skills/channel-mix.md"}


def test_the_model_around_it_is_not_grounding(grounded):
    """The store hands over the whole model, cubes and views included."""
    assert len(grounded.rules + grounded.skills + grounded.certified) == 4


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


def test_yaml_under_grounding_is_never_loaded():
    """The store refuses to store one, and this is the other half of that: Cube compiles any
    `*.yml` under the model, and one here takes the whole thing to zero cubes. Nothing should
    ever make writing one look useful."""
    files = {"grounding/certified_queries/monthly.yml": "name: monthly\nquery: {}\n"}
    assert Grounding(files).certified == []


def test_every_artefact_directory_is_read():
    """The end-to-end pin: an artefact in each directory the scaffolding names has to come back
    out. Reading a different set would leave rules that silently never load."""
    files = {
        f"{GROUNDING_ROOT}/{artefact}/x.md": f"---\nname: {artefact}\n---\n\nbody\n"
        for artefact in ARTEFACT_DIRS
    }
    grounding = Grounding(files)
    found = {a.name for a in grounding.rules + grounding.skills + grounding.certified}
    assert found == set(ARTEFACT_DIRS)


# --- the absence of grounding ------------------------------------------------


def test_a_model_with_no_grounding_is_empty():
    assert Grounding({}).is_empty
    assert Grounding({"cubes/orders.yml": "cubes: []\n"}).is_empty


def test_populated_grounding_is_not_empty(grounded):
    assert not grounded.is_empty


# --- reading it from the store -----------------------------------------------


@respx.mock
async def test_load_reads_the_ref_it_is_asked_for(configured):
    route = respx.get(f"{URL}/files", params={"ref": "edit-sales"}).mock(
        return_value=httpx.Response(200, json={"sha": "abc", "files": FILES})
    )
    assert not (await loader.load("edit-sales")).is_empty
    assert route.called


@respx.mock
async def test_load_defaults_to_main(configured):
    """Pinned, because a question chat and a dashboard must never silently read an edit ref."""
    route = respx.get(f"{URL}/files", params={"ref": "main"}).mock(
        return_value=httpx.Response(200, json={"sha": "abc", "files": {}})
    )
    assert (await loader.load()).is_empty
    assert route.called


@respx.mock
async def test_a_ref_that_is_not_there_grounds_on_nothing(configured):
    """A candidate that was cleaned up, or a ref this store never had — the same answer a
    directory nobody had created used to give."""
    respx.get(f"{URL}/files", params={"ref": "gone"}).mock(
        return_value=httpx.Response(404, json={"detail": "no such ref: 'gone'"})
    )
    assert (await loader.load("gone")).is_empty


async def test_a_deployment_with_no_store_grounds_on_nothing(monkeypatch):
    """Every deployment until one is configured, and `load` runs on every chat turn — so
    raising here took out chat, MCP and the sources page at once."""

    async def storeless():
        return Deployment()

    monkeypatch.setattr("analyst_agent.modelstore.client.current", storeless)
    assert (await loader.load()).is_empty


@respx.mock
async def test_a_store_that_does_not_answer_raises(configured):
    """Not the same thing as empty grounding: an agent quietly dropping the rules it is bound
    by is worse than a turn that says the store is down."""
    respx.get(f"{URL}/files", params={"ref": "main"}).mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(StoreError) as caught:
        await loader.load()
    assert not caught.value.missing
