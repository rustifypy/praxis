"""The layout rules, and the invariant that dashboard events carry no state.

The tools are exercised through their real coroutines with a real store on a temp file —
no Cube and no model, so this is still the deterministic half. `emit` is monkeypatched
because `get_stream_writer` is a no-op outside a graph, which is exactly the case the
event-shape assertions need to observe.
"""

import pytest

from analyst_agent.dashboards import layout, tools
from analyst_agent.dashboards.store import DashboardStore, Tile

QUERY = {"measures": ["orders.net_revenue"], "dimensions": ["orders.status"]}
BAR = {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}
KPI_QUERY = {"measures": ["orders.count"]}
KPI = {"type": "kpi", "y": ["orders.count"]}


def fake_tile(tile_id: str, kind: str = "bar") -> Tile:
    return Tile(
        id=tile_id,
        dashboard_id="d1",
        title=f"tile {tile_id}",
        query=QUERY,
        chart={"type": kind},
        time_member="",
        created_at=0.0,
        updated_at=0.0,
    )


# --- layout ------------------------------------------------------------------------


def test_a_partial_layout_appends_the_rest_rather_than_hiding_it():
    live = [fake_tile("a"), fake_tile("b"), fake_tile("c")]
    checked, errors = layout.validate({"columns": 2, "items": [{"tile": "c", "span": 1}]}, live)

    assert errors == []
    # "Put c first" must not delete a and b.
    assert [i["tile"] for i in checked["items"]] == ["c", "a", "b"]


def test_a_layout_naming_a_tile_that_is_not_there_is_rejected():
    checked, errors = layout.validate({"items": [{"tile": "ghost"}]}, [fake_tile("a")])
    assert checked is None
    assert "not on this dashboard" in errors[0]


def test_a_tile_cannot_be_placed_twice():
    live = [fake_tile("a")]
    checked, errors = layout.validate(
        {"items": [{"tile": "a", "span": 1}, {"tile": "a", "span": 1}]}, live
    )
    assert checked is None
    assert "placed twice" in errors[0]


def test_a_span_wider_than_the_grid_is_rejected():
    checked, errors = layout.validate(
        {"columns": 2, "items": [{"tile": "a", "span": 3}]}, [fake_tile("a")]
    )
    assert checked is None
    assert "span for" in errors[0]


def test_more_columns_than_a_chart_stays_readable_in_is_rejected():
    checked, errors = layout.validate({"columns": 9, "items": [{"tile": "a"}]}, [fake_tile("a")])
    assert checked is None
    assert "columns must be" in errors[0]


def test_the_default_layout_leads_with_the_kpis():
    live = [fake_tile("chart"), fake_tile("total", kind="kpi"), fake_tile("grid", kind="table")]
    spec = layout.default_layout(live)

    assert [i["tile"] for i in spec["items"]] == ["total", "chart", "grid"]
    # A table's columns are its content, so it takes the whole row.
    assert [i["span"] for i in spec["items"]] == [1, 1, 2]


# --- the tools ---------------------------------------------------------------------


@pytest.fixture
def events(monkeypatch):
    """Collect what the dashboard tools emit."""
    captured: list[dict] = []
    monkeypatch.setattr(tools, "emit", lambda event: captured.append(event.model_dump()))
    return captured


@pytest.fixture
async def kit(tmp_path, catalog):
    store = DashboardStore(tmp_path / "analyst.db")
    await store.init()
    ctx = tools.DashboardContext(store=store, catalog=catalog, source_name="chinook")
    return store, {t.name: t for t in tools.build_dashboard_tools(ctx)}


async def test_a_tile_is_saved_with_the_time_member_a_date_control_would_target(kit, events):
    store, tool = kit
    board = await store.create("Revenue", "chinook")
    query = {
        "measures": ["orders.net_revenue"],
        "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month"}],
    }
    chart = {"type": "line", "x": "orders.created_at.month", "y": ["orders.net_revenue"]}

    out = await tool["add_tile"].ainvoke(
        {"dashboard_id": board.id, "title": "Monthly", "query": query, "chart": chart}
    )

    assert "Added" in out
    assert (await store.tiles(board.id))[0].time_member == "orders.created_at"


async def test_a_tile_naming_a_member_that_does_not_exist_is_rejected(kit, events):
    store, tool = kit
    board = await store.create("Revenue", "chinook")

    out = await tool["add_tile"].ainvoke(
        {
            "dashboard_id": board.id,
            "title": "Broken",
            "query": {"measures": ["orders.revenue"]},
            "chart": {"type": "kpi", "y": ["orders.revenue"]},
        }
    )

    assert "Tile rejected" in out
    assert await store.tiles(board.id) == []


async def test_a_fixed_window_is_saved_and_reported_as_pinned(kit, events):
    store, tool = kit
    board = await store.create("Revenue", "chinook")

    out = await tool["add_tile"].ainvoke(
        {
            "dashboard_id": board.id,
            "title": "Q1",
            "query": {
                "measures": ["orders.net_revenue"],
                "timeDimensions": [
                    {
                        "dimension": "orders.created_at",
                        "granularity": "month",
                        "dateRange": ["2026-01-01", "2026-03-31"],
                    }
                ],
            },
            "chart": {"type": "line", "x": "orders.created_at.month", "y": ["orders.net_revenue"]},
        }
    )

    assert len(await store.tiles(board.id)) == 1
    assert "refreshing will not move it" in out


async def test_a_removed_tile_is_recoverable_by_reverting_a_layout(kit, events):
    store, tool = kit
    board = await store.create("Revenue", "chinook")
    first = await store.add_tile(board.id, "one", QUERY, BAR)
    second = await store.add_tile(board.id, "two", QUERY, BAR)

    await tool["set_layout"].ainvoke(
        {
            "dashboard_id": board.id,
            "items": [{"tile": first.id, "span": 1}, {"tile": second.id, "span": 1}],
            "note": "initial",
        }
    )
    await tool["remove_tile"].ainvoke({"tile_id": second.id})
    assert len(await store.tiles(board.id)) == 1

    # Reverting drops the tile that is gone rather than failing outright.
    out = await tool["revert_layout"].ainvoke({"dashboard_id": board.id, "version": 1})
    assert "no longer exist" in out
    assert (await store.layout(board.id))["version"] == 2


async def test_reverting_appends_rather_than_rewinding_so_it_is_itself_undoable(kit, events):
    store, tool = kit
    board = await store.create("Revenue", "chinook")
    first = await store.add_tile(board.id, "one", QUERY, BAR)
    second = await store.add_tile(board.id, "two", QUERY, BAR)
    pair = [{"tile": first.id, "span": 1}, {"tile": second.id, "span": 1}]

    await tool["set_layout"].ainvoke({"dashboard_id": board.id, "items": pair, "note": "a"})
    await tool["set_layout"].ainvoke(
        {"dashboard_id": board.id, "items": list(reversed(pair)), "note": "b"}
    )
    await tool["revert_layout"].ainvoke({"dashboard_id": board.id, "version": 1})

    assert [v["version"] for v in await store.layout_history(board.id)] == [3, 2, 1]
    assert [i["tile"] for i in (await store.layout(board.id))["spec"]["items"]] == [
        first.id,
        second.id,
    ]


async def test_a_dashboard_cannot_be_created_with_no_active_source(tmp_path, catalog, events):
    store = DashboardStore(tmp_path / "analyst.db")
    await store.init()
    ctx = tools.DashboardContext(store=store, catalog=catalog, source_name="")
    tool = {t.name: t for t in tools.build_dashboard_tools(ctx)}

    out = await tool["create_dashboard"].ainvoke({"title": "Nowhere"})

    assert "Nothing was created" in out
    assert await store.list_dashboards() == []


async def test_no_dashboard_tool_can_refresh_one(kit):
    _, tool = kit
    # Executing twelve stored queries is a page action, not a turn. The absence is the
    # feature: a tool named for it would make "tidy this up" cost 24 warehouse calls.
    assert not [name for name in tool if "refresh" in name or "run" in name]


async def test_dashboard_events_never_carry_state(kit, events):
    """The intent-only rule. A tool trace that recorded a layout would go stale the next
    time anything was edited, because the row is the truth and the event is not."""
    store, tool = kit
    board = await store.create("Revenue", "chinook")
    tile = await store.add_tile(board.id, "one", QUERY, BAR)

    await tool["list_dashboards"].ainvoke({})
    await tool["read_dashboard"].ainvoke({"dashboard_id": board.id})
    await tool["add_tile"].ainvoke(
        {"dashboard_id": board.id, "title": "two", "query": KPI_QUERY, "chart": KPI}
    )
    await tool["update_tile"].ainvoke({"tile_id": tile.id, "title": "renamed"})
    await tool["set_layout"].ainvoke(
        {"dashboard_id": board.id, "items": [{"tile": tile.id, "span": 1}], "note": "tidy"}
    )
    await tool["remove_tile"].ainvoke({"tile_id": tile.id})

    assert events, "the tools emitted nothing at all"
    for event in events:
        extra = set(event["data"]) - tools.EVENT_KEYS
        assert not extra, f"{event['data'].get('tool')} leaked {extra} into the transcript"


async def test_a_tool_works_when_it_is_driven_outside_a_graph(kit):
    """No `events` fixture, deliberately — this is the path the fixture hides.

    Every other test here monkeypatches `emit` to observe what a tool says, which means the
    real `emit` was never exercised. Driving a tool's own `.ainvoke()` outside a graph makes
    `get_stream_writer` raise `KeyError('__pregel_runtime')` rather than the documented
    `RuntimeError`, and a bare `except RuntimeError` let that through and killed the call.
    """
    store, tool = kit
    board = await store.create("Revenue", "chinook")

    out = await tool["add_tile"].ainvoke(
        {"dashboard_id": board.id, "title": "Total", "query": KPI_QUERY, "chart": KPI}
    )

    assert "Added" in out
    assert len(await store.tiles(board.id)) == 1
