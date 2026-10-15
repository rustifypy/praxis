"""`validate` and `refresh`, over a fake Cube session and a real store on a temp file.

The atomic pass and the refused/failed split had no tests at all — the pure functions around
them (`outgrown`, `deltas`, `layout.validate`) were covered and their only caller was not.
"""

import pytest

from praxis.api.dashboard import controller as runner
from praxis.core import boards as _dashboards
from praxis.core import db

QUERY = {"measures": ["orders.net_revenue"], "dimensions": ["orders.status"]}
BAR = {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}
ROWS = [{"orders.status": "paid", "orders.net_revenue": 100}]


@pytest.fixture
async def boards(tmp_path):
    await db.create_tables()
    store = _dashboards
    return store


async def _board(store, *queries: dict) -> str:
    board = await store.create("Revenue")
    for i, query in enumerate(queries):
        await store.add_tile(board.id, f"tile {i}", query, BAR)
    return board.id


# --- a board that is not there ------------------------------------------------


async def test_an_unknown_dashboard_fails_rather_than_refusing(boards, cube):
    outcome = await runner.refresh("nope", cube.served, cube, 5_000)
    assert outcome.status == "failed"
    assert "no dashboard" in outcome.error


# --- the atomic pass ---------------------------------------------------------


async def test_one_tile_that_cannot_compile_stops_every_tile_executing(boards, cube):
    """The invariant: every tile compiles before any tile executes, so a board never renders
    half a refresh."""
    board = await _board(boards, QUERY, {"measures": ["orders.nope"]}, QUERY)

    outcome = await runner.refresh(board, cube.served, cube, 5_000)

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

    outcome = await runner.refresh(board, cube.served, cube, 5_000)

    assert outcome.status == "failed"
    assert cube.compiled and cube.loaded
    assert "relation does not exist" in outcome.tiles[0].errors[0]
    assert (await boards.runs(board, limit=1))[0].status == "failed"


async def test_validate_records_no_run_and_reads_no_data(boards, cube):
    cube.rows = ROWS
    board = await _board(boards, QUERY)

    outcome = await runner.validate(board, cube.served, cube, 5_000)

    assert outcome.ok and outcome.status == "ok"
    assert outcome.run_id == ""
    assert cube.loaded == []
    assert await boards.runs(board) == []


async def test_an_empty_dashboard_validates_but_will_not_refresh(boards, cube):
    """Pinning a disagreement rather than endorsing it: the page reads `available: true` from
    validate and its Refresh button then returns `refused`."""
    board = await boards.create("Empty")

    assert (await runner.validate(board.id, cube.served, cube, 5_000)).status == "ok"
    refreshed = await runner.refresh(board.id, cube.served, cube, 5_000)
    assert refreshed.status == "refused"
    assert "no tiles" in refreshed.error


# --- what a successful refresh leaves behind ---------------------------------


async def test_a_refresh_stores_its_rows_and_the_window_cube_resolved(boards, cube):
    cube.rows = ROWS
    cube.resolved_ranges = {"orders.created_at": ["2026-01-01", "2026-01-31"]}
    board = await _board(boards, QUERY)

    outcome = await runner.refresh(board, cube.served, cube, 5_000)

    assert outcome.ok
    stored = await boards.results_for_run(outcome.run_id)
    assert stored[0].rows == ROWS
    assert stored[0].row_count == 1
    assert stored[0].resolved_ranges == cube.resolved_ranges


async def test_the_second_refresh_reports_change_against_the_first(boards, cube):
    """`deltas` is read before this run's own result is saved, so the newest stored entry is
    genuinely the previous refresh rather than this one."""
    board = await _board(boards, {"measures": ["orders.net_revenue"]})
    cube.rows = [{"orders.net_revenue": 100}]
    await runner.refresh(board, cube.served, cube, 5_000)
    cube.rows = [{"orders.net_revenue": 150}]

    outcome = await runner.refresh(board, cube.served, cube, 5_000)

    change = outcome.tiles[0].deltas[0]
    assert (change.value, change.previous, change.percent) == (150, 100, 50.0)


async def test_a_chart_is_re_judged_against_the_rows_that_arrived(boards, cube):
    """A tile authored as twelve bars is four hundred by December, and nobody re-authors it."""
    cube.rows = [{"orders.status": f"s{i}", "orders.net_revenue": i} for i in range(300)]
    board = await _board(boards, QUERY)

    outcome = await runner.refresh(board, cube.served, cube, 5_000)

    assert outcome.ok
    assert "past the" in outcome.tiles[0].degraded
