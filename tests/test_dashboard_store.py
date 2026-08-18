import pytest

from analyst_agent.dashboards.store import DashboardStore

QUERY = {"measures": ["revenue.net_revenue"], "dimensions": ["revenue.status"]}
CHART = {"type": "bar", "x": "revenue.status", "y": ["revenue.net_revenue"]}
COLUMNS = [{"id": "revenue.status", "label": "Status", "kind": "dimension"}]


@pytest.fixture
async def store(tmp_path):
    s = DashboardStore(tmp_path / "analyst.db")
    await s.init()
    return s


@pytest.fixture
async def board(store):
    return await store.create("Revenue")


async def test_dashboards_are_listed_most_recently_touched_first(store):
    first = await store.create("first")
    second = await store.create("second")
    await store.add_tile(first.id, "a tile", QUERY, CHART)

    assert [d.id for d in await store.list_dashboards()] == [first.id, second.id]


async def test_a_tile_round_trips_its_query_and_chart(store, board):
    tile = await store.add_tile(board.id, "By status", QUERY, CHART, time_member="revenue.at")
    stored = await store.tile(tile.id)

    assert stored.query == QUERY
    assert stored.chart == CHART
    assert stored.time_member == "revenue.at"


async def test_a_deleted_tile_is_hidden_but_recoverable(store, board):
    tile = await store.add_tile(board.id, "By status", QUERY, CHART)
    await store.set_tile_deleted(tile.id)

    assert await store.tiles(board.id) == []
    assert len(await store.tiles(board.id, include_deleted=True)) == 1

    # The point of the soft delete: reverting a layout that names this tile brings it back.
    await store.set_tile_deleted(tile.id, deleted=False)
    assert len(await store.tiles(board.id)) == 1


async def test_updating_a_tile_leaves_the_fields_it_was_not_given(store, board):
    tile = await store.add_tile(board.id, "By status", QUERY, CHART)
    await store.update_tile(tile.id, chart={"type": "table"})
    stored = await store.tile(tile.id)

    assert stored.chart == {"type": "table"}
    assert stored.query == QUERY
    assert stored.title == "By status"


async def test_layouts_append_and_the_current_one_is_the_highest_version(store, board):
    assert await store.layout(board.id) is None

    first = await store.save_layout(board.id, {"order": ["a", "b"]}, note="initial")
    second = await store.save_layout(board.id, {"order": ["b", "a"]}, note="swapped")

    assert (first, second) == (1, 2)
    assert (await store.layout(board.id))["spec"] == {"order": ["b", "a"]}
    # Undo reads an older version and appends it as a new one, so history never shrinks.
    assert (await store.layout(board.id, version=1))["spec"] == {"order": ["a", "b"]}
    assert [v["note"] for v in await store.layout_history(board.id)] == ["swapped", "initial"]


async def test_a_refused_run_keeps_its_reasons(store, board):
    run = await store.start_run(board.id)
    assert run.status == "validating"

    await store.finish_run(
        run.id, "refused", errors=[{"tile_id": "t1", "errors": ["'x.y' does not exist"]}]
    )
    stored = (await store.runs(board.id))[0]

    assert stored.status == "refused"
    assert stored.errors[0]["errors"] == ["'x.y' does not exist"]
    assert stored.finished_at is not None


async def test_an_unknown_run_status_is_refused_rather_than_stored(store, board):
    """By the `Status` annotation on both writers, which is why neither tests the tuple. A
    status nothing reads would leave `latest_run("ok")` unable to find the run it just wrote."""
    run = await store.start_run(board.id)
    for write in (store.finish_run, store.set_run_status):
        with pytest.raises(ValueError, match="'validating', 'running', 'ok'"):
            await write(run.id, "done")


async def test_a_dashboard_renders_its_last_successful_run_not_its_last_attempt(store, board):
    good = await store.start_run(board.id)
    await store.finish_run(good.id, "ok")
    bad = await store.start_run(board.id)
    await store.finish_run(bad.id, "refused")

    assert (await store.runs(board.id))[0].id == bad.id
    assert (await store.latest_run(board.id)).id == good.id


async def test_results_append_so_a_tile_can_be_compared_with_its_previous_refresh(store, board):
    tile = await store.add_tile(board.id, "By status", QUERY, CHART)
    for total in (100, 120):
        run = await store.start_run(board.id)
        await store.save_tile_result(run.id, tile.id, COLUMNS, [{"revenue.net_revenue": total}])
        await store.finish_run(run.id, "ok")

    history = await store.tile_history(tile.id)
    assert [h.rows[0]["revenue.net_revenue"] for h in history] == [120, 100]


async def test_the_resolved_window_survives_the_round_trip(store, board):
    tile = await store.add_tile(board.id, "Monthly", QUERY, CHART)
    run = await store.start_run(board.id)
    await store.save_tile_result(
        run.id,
        tile.id,
        COLUMNS,
        [{"revenue.net_revenue": 1}],
        resolved_ranges={"revenue.at": ["2026-07-12T00:00:00.000", "2026-08-10T23:59:59.999"]},
        duration_ms=42,
    )

    stored = (await store.tile_history(tile.id))[0]
    assert stored.resolved_ranges["revenue.at"][0] == "2026-07-12T00:00:00.000"
    assert stored.duration_ms == 42


async def test_pruning_drops_old_rows_and_keeps_the_record_of_the_run(store, board):
    tile = await store.add_tile(board.id, "By status", QUERY, CHART)
    for total in (1, 2, 3, 4):
        run = await store.start_run(board.id)
        await store.save_tile_result(run.id, tile.id, COLUMNS, [{"revenue.net_revenue": total}])
        await store.finish_run(run.id, "ok")

    assert await store.prune(board.id, keep=2) == 2

    history = await store.tile_history(tile.id, limit=10)
    assert [h.rows is None for h in history] == [False, False, True, True]
    # The run still happened, and how many rows it returned is still on record.
    assert [h.row_count for h in history] == [1, 1, 1, 1]


async def test_pruning_is_idempotent(store, board):
    tile = await store.add_tile(board.id, "By status", QUERY, CHART)
    for _ in range(3):
        run = await store.start_run(board.id)
        await store.save_tile_result(run.id, tile.id, COLUMNS, [{"revenue.net_revenue": 1}])

    assert await store.prune(board.id, keep=2) == 1
    assert await store.prune(board.id, keep=2) == 0


async def test_pruning_counts_runs_per_tile_not_per_dashboard(store, board):
    left = await store.add_tile(board.id, "left", QUERY, CHART)
    right = await store.add_tile(board.id, "right", QUERY, CHART)
    for _ in range(3):
        run = await store.start_run(board.id)
        for tile in (left, right):
            await store.save_tile_result(run.id, tile.id, COLUMNS, [{"x": 1}])

    # Three runs, two tiles: six results, and keep=2 leaves two *per tile*.
    assert await store.prune(board.id, keep=2) == 2
    assert len([h for h in await store.tile_history(left.id, limit=10) if h.rows]) == 2
    assert len([h for h in await store.tile_history(right.id, limit=10) if h.rows]) == 2


async def test_deleting_a_dashboard_takes_its_tiles_runs_and_results(store, board):
    keep = await store.create("keep")
    kept = await store.add_tile(keep.id, "kept", QUERY, CHART)
    tile = await store.add_tile(board.id, "By status", QUERY, CHART)
    run = await store.start_run(board.id)
    await store.save_tile_result(run.id, tile.id, COLUMNS, [{"x": 1}])
    await store.save_layout(board.id, {"order": [tile.id]})

    await store.delete(board.id)

    assert await store.get(board.id) is None
    assert await store.tiles(board.id, include_deleted=True) == []
    assert await store.runs(board.id) == []
    assert await store.layout(board.id) is None
    assert await store.tile_history(tile.id) == []
    # And nothing else went with it.
    assert (await store.tile(kept.id)).title == "kept"
