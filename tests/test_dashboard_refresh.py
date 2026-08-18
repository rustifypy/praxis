"""`validate` and `refresh`, over a fake Cube session and a real store on a temp file.

The atomic pass and the refused/failed split had no tests at all — the pure functions around
them (`outgrown`, `deltas`, `layout.validate`) were covered and their only caller was not.
`insights.summarise` is stubbed: it is the one model call in a refresh.
"""

import pytest

from analyst_agent.dashboards import refresh as runner
from analyst_agent.dashboards.store import DashboardStore, Tile

QUERY = {"measures": ["orders.net_revenue"], "dimensions": ["orders.status"]}
BAR = {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}
ROWS = [{"orders.status": "paid", "orders.net_revenue": 100}]


@pytest.fixture
async def boards(tmp_path):
    store = DashboardStore(tmp_path / "analyst.db")
    await store.init()
    return store


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setattr(runner.insights, "summarise", _canned)


async def _canned(title, tiles) -> tuple[str, dict[str, str]]:
    """The paragraph and a caption per tile id — one call produces both."""
    return "revenue held steady", {tile[0]: f"{tile[1]} caption" for tile in tiles}


async def _board(store: DashboardStore, *queries: dict) -> str:
    board = await store.create("Revenue")
    for i, query in enumerate(queries):
        await store.add_tile(board.id, f"tile {i}", query, BAR)
    return board.id


# --- a board that is not there ------------------------------------------------


async def test_an_unknown_dashboard_fails_rather_than_refusing(boards, cube):
    outcome = await runner.refresh("nope", boards, cube.served, cube, 5_000)
    assert outcome.status == "failed"
    assert "no dashboard" in outcome.error


# --- the atomic pass ---------------------------------------------------------


async def test_one_tile_that_cannot_compile_stops_every_tile_executing(boards, cube):
    """The invariant: every tile compiles before any tile executes, so a board never renders
    half a refresh."""
    board = await _board(boards, QUERY, {"measures": ["orders.nope"]}, QUERY)

    outcome = await runner.refresh(board, boards, cube.served, cube, 5_000)

    assert outcome.status == "refused"
    assert cube.loaded == []
    assert [t.ok for t in outcome.tiles] == [True, False, True]
    run = await boards.runs(board, limit=1)
    assert run[0].status == "refused"
    assert await boards.results_for_run(run[0].id) == []


async def test_a_tile_that_breaks_after_compiling_is_failed_not_refused(boards, cube):
    """The two are kept apart because only one is fixable by editing the tile."""
    cube.fail_load = "relation does not exist"
    board = await _board(boards, QUERY)

    outcome = await runner.refresh(board, boards, cube.served, cube, 5_000)

    assert outcome.status == "failed"
    assert cube.compiled and cube.loaded
    assert "relation does not exist" in outcome.tiles[0].errors[0]
    assert (await boards.runs(board, limit=1))[0].status == "failed"


async def test_validate_records_no_run_and_reads_no_data(boards, cube):
    cube.rows = ROWS
    board = await _board(boards, QUERY)

    outcome = await runner.validate(board, boards, cube.served, cube, 5_000)

    assert outcome.ok and outcome.status == "ok"
    assert outcome.run_id == ""
    assert cube.loaded == []
    assert await boards.runs(board) == []


async def test_an_empty_dashboard_validates_but_will_not_refresh(boards, cube):
    """Pinning a disagreement rather than endorsing it: the page reads `available: true` from
    validate and its Refresh button then returns `refused`."""
    board = await boards.create("Empty")

    assert (await runner.validate(board.id, boards, cube.served, cube, 5_000)).status == "ok"
    refreshed = await runner.refresh(board.id, boards, cube.served, cube, 5_000)
    assert refreshed.status == "refused"
    assert "no tiles" in refreshed.error


# --- what a successful refresh leaves behind ---------------------------------


async def test_a_refresh_stores_its_rows_and_the_window_cube_resolved(boards, cube):
    cube.rows = ROWS
    cube.resolved_ranges = {"orders.created_at": ["2026-01-01", "2026-01-31"]}
    board = await _board(boards, QUERY)

    outcome = await runner.refresh(board, boards, cube.served, cube, 5_000)

    assert outcome.ok and outcome.insight == "revenue held steady"
    stored = await boards.results_for_run(outcome.run_id)
    assert stored[0].rows == ROWS
    assert stored[0].row_count == 1
    assert stored[0].resolved_ranges == cube.resolved_ranges


async def test_every_tile_gets_its_caption_from_the_run_that_produced_its_rows(boards, cube):
    """One model call returns the paragraph and every caption, and each caption lands on the
    row it describes rather than on the board."""
    cube.rows = ROWS
    board = await _board(boards, QUERY, QUERY)

    outcome = await runner.refresh(board, boards, cube.served, cube, 5_000)

    stored = {r.tile_id: r.insight for r in await boards.results_for_run(outcome.run_id)}
    assert sorted(stored.values()) == ["tile 0 caption", "tile 1 caption"]


# --- the board's window ------------------------------------------------------


def _timed(**kwargs) -> Tile:
    return Tile(
        id="t",
        dashboard_id="d",
        title="t",
        chart=BAR,
        created_at=0.0,
        updated_at=0.0,
        **kwargs,
    )


def test_the_window_is_written_into_the_primary_time_member_and_nothing_else():
    """A tile can filter on more than one date. Only the member `time_member` names is the one
    a board-level window means."""
    tile = _timed(
        query={
            "measures": ["orders.net_revenue"],
            "timeDimensions": [
                {"dimension": "orders.created_at", "granularity": "month"},
                {"dimension": "orders.shipped_at", "dateRange": ["2020-01-01", "2020-02-01"]},
            ],
        },
        time_member="orders.created_at",
    )

    out = runner.windowed(tile, "last 30 days")

    assert out["timeDimensions"][0]["dateRange"] == "last 30 days"
    assert out["timeDimensions"][1]["dateRange"] == ["2020-01-01", "2020-02-01"]
    # The stored query is what a tile is; a window is only ever applied to the copy that runs.
    assert "dateRange" not in tile.query["timeDimensions"][0]


def test_a_tile_with_no_time_member_runs_unchanged_under_a_window():
    """Which is why the browser is told how many tiles a window reaches, rather than assuming
    it reaches all of them."""
    tile = _timed(query={"measures": ["orders.net_revenue"]}, time_member="")
    assert runner.windowed(tile, "last 30 days") is tile.query


async def test_the_board_window_reaches_the_query_that_executes(boards, cube):
    cube.rows = ROWS
    board = (await boards.create("Revenue")).id
    await boards.add_tile(
        board,
        "timed",
        {
            "measures": ["orders.net_revenue"],
            "timeDimensions": [{"dimension": "orders.created_at"}],
        },
        BAR,
        time_member="orders.created_at",
    )
    await boards.set_window(board, "last 30 days")

    await runner.refresh(board, boards, cube.served, cube, 5_000)

    # Compiled as well as loaded: a window the page-open check does not see is a board that
    # passes its checks and then fails on refresh.
    assert cube.compiled[0]["timeDimensions"][0]["dateRange"] == "last 30 days"
    assert cube.loaded[0]["timeDimensions"][0]["dateRange"] == "last 30 days"


async def test_clearing_the_window_puts_every_tile_back_on_its_own_range(boards, cube):
    cube.rows = ROWS
    board = (await boards.create("Revenue")).id
    await boards.add_tile(
        board,
        "timed",
        {
            "measures": ["orders.net_revenue"],
            "timeDimensions": [{"dimension": "orders.created_at"}],
        },
        BAR,
        time_member="orders.created_at",
    )
    await boards.set_window(board, "last 30 days")
    await boards.set_window(board, None)

    await runner.refresh(board, boards, cube.served, cube, 5_000)

    assert "dateRange" not in cube.loaded[0]["timeDimensions"][0]


async def test_the_second_refresh_reports_change_against_the_first(boards, cube):
    """`deltas` is read before this run's own result is saved, so the newest stored entry is
    genuinely the previous refresh rather than this one."""
    board = await _board(boards, {"measures": ["orders.net_revenue"]})
    cube.rows = [{"orders.net_revenue": 100}]
    await runner.refresh(board, boards, cube.served, cube, 5_000)
    cube.rows = [{"orders.net_revenue": 150}]

    outcome = await runner.refresh(board, boards, cube.served, cube, 5_000)

    change = outcome.tiles[0].deltas[0]
    assert (change.value, change.previous, change.percent) == (150, 100, 50.0)


async def test_a_chart_is_re_judged_against_the_rows_that_arrived(boards, cube):
    """A tile authored as twelve bars is four hundred by December, and nobody re-authors it."""
    cube.rows = [{"orders.status": f"s{i}", "orders.net_revenue": i} for i in range(300)]
    board = await _board(boards, QUERY)

    outcome = await runner.refresh(board, boards, cube.served, cube, 5_000)

    assert outcome.ok
    assert "past the" in outcome.tiles[0].degraded
