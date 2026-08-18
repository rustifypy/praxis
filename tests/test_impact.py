"""What a branch would break. Deterministic — the catalog is a fixture, no container."""

import pytest

from analyst_agent.dashboards.store import DashboardStore
from analyst_agent.sources.editing import impact
from analyst_agent.sources.grounding import Grounding

REVENUE = {"measures": ["revenue.net_revenue"], "dimensions": ["revenue.status"]}


@pytest.fixture
async def boards(tmp_path):
    store = DashboardStore(tmp_path / "analyst.db")
    await store.init()
    board = await store.create("Sales")
    await store.add_tile(board.id, "Revenue by status", REVENUE, {"type": "bar"})
    return store


def empty(tmp_path) -> Grounding:
    """No grounding at all — a directory nothing has been written into."""
    return Grounding(tmp_path / "nothing")


def certified(tmp_path, query: str) -> Grounding:
    root = tmp_path / "grounding"
    (root / "certified_queries").mkdir(parents=True)
    (root / "certified_queries" / "revenue.md").write_text(
        f"---\nname: monthly revenue\nuser_request: revenue by month\n---\n```json\n{query}\n```\n"
    )
    return Grounding(root)


async def test_nothing_breaks_when_every_member_survives(catalog, boards, tmp_path):
    report = await impact.check(catalog, boards, empty(tmp_path))
    assert report.ok
    assert report.checked == 1


async def test_a_tile_naming_a_dropped_member_is_reported(catalog, boards, tmp_path):
    """`revenue` is a view in the fixture catalog, so dropping its alias is exactly the rename
    a branch is allowed to make — and exactly what a tile cannot survive."""
    trimmed = type(catalog)([e for e in catalog.entities if e.name != "revenue"])
    report = await impact.check(trimmed, boards, empty(tmp_path))
    assert not report.ok
    broken = report.broken[0]
    assert broken.kind == "tile"
    assert broken.where == "Sales" and broken.title == "Revenue by status"
    assert broken.members == ["revenue.net_revenue", "revenue.status"]


async def test_the_report_suggests_what_is_nearest(catalog, boards, tmp_path):
    trimmed = type(catalog)([e for e in catalog.entities if e.name != "revenue"])
    report = await impact.check(trimmed, boards, empty(tmp_path))
    assert "orders.net_revenue" in report.broken[0].nearest["revenue.net_revenue"]
    assert "no longer serves" in report.as_message()


async def test_a_deleted_tile_is_not_checked(catalog, boards, tmp_path):
    board = next(b for b in await boards.list_dashboards() if b.title == "Sales")
    tile = (await boards.tiles(board.id))[0]
    await boards.set_tile_deleted(tile.id)
    report = await impact.check(type(catalog)([]), boards, empty(tmp_path))
    assert report.ok and report.checked == 0


async def test_a_certified_query_is_checked_too(catalog, boards, tmp_path):
    grounding = certified(tmp_path, '{"measures": ["revenue.gone"]}')
    report = await impact.check(catalog, boards, grounding)
    assert [b.kind for b in report.broken] == ["certified"]
    assert report.broken[0].members == ["revenue.gone"]


async def test_a_certified_query_that_is_not_json_is_skipped(catalog, boards, tmp_path):
    grounding = certified(tmp_path, "not json at all")
    report = await impact.check(catalog, boards, grounding)
    assert report.ok


def test_a_query_that_no_longer_parses_is_left_to_the_refresh_gate(catalog):
    """`extra="forbid"`, so this is a real possibility — and reporting it as a missing member
    would misdescribe it."""
    absent, nearest = impact.missing_members({"measures": ["x"], "nonsense": 1}, catalog)
    assert absent == [] and nearest == {}


def test_filters_and_time_dimensions_count_as_references(catalog):
    query = {
        "measures": ["orders.count"],
        "timeDimensions": [{"dimension": "orders.gone_at", "granularity": "month"}],
        "filters": [{"member": "orders.missing", "operator": "equals", "values": ["x"]}],
    }
    absent, _ = impact.missing_members(query, catalog)
    assert absent == ["orders.gone_at", "orders.missing"]
