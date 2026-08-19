from analyst_agent.cube.shape import Column
from analyst_agent.dashboards.delta import deltas
from analyst_agent.dashboards.store import TileResult

REVENUE = Column(
    id="orders.net_revenue", label="Revenue", kind="measure", type="sum", format="", align="right"
)
COUNT = Column(
    id="orders.count", label="Orders", kind="measure", type="count", format="", align="right"
)
STATUS = Column(
    id="orders.status", label="Status", kind="dimension", type="string", format="", align="left"
)


def result(rows, at: float = 100.0) -> TileResult:
    return TileResult(
        id="r",
        run_id="run",
        tile_id="t",
        columns=[],
        rows=rows,
        row_count=len(rows) if rows is not None else 0,
        resolved_ranges={},
        duration_ms=1,
        created_at=at,
    )


def test_a_single_row_tile_reports_change_per_measure():
    now = result([{"orders.net_revenue": 112.0, "orders.count": 8}])
    was = result([{"orders.net_revenue": 100.0, "orders.count": 10}], at=50.0)

    changes, since = deltas(now.rows, was, [REVENUE, COUNT])

    assert [(d.label, d.change, round(d.percent or 0, 1)) for d in changes] == [
        ("Revenue", 12.0, 12.0),
        ("Orders", -2, -20.0),
    ]
    # The interval is the gap between refreshes, not a calendar period — the caller has to
    # be able to say so, so the timestamp comes back with the numbers.
    assert since == 50.0


def test_a_grouped_result_gets_no_delta_rather_than_one_picked_from_a_row():
    now = result([{"orders.status": "paid", "orders.net_revenue": 5}, {"orders.status": "open"}])
    was = result([{"orders.status": "paid", "orders.net_revenue": 3}, {"orders.status": "open"}])
    assert deltas(now.rows, was, [STATUS, REVENUE]) == ([], None)


def test_a_first_refresh_has_nothing_to_compare_with():
    assert deltas([{"orders.count": 3}], None, [COUNT]) == ([], None)


def test_a_pruned_previous_result_is_not_comparable():
    pruned = result(None)
    assert deltas([{"orders.count": 3}], pruned, [COUNT]) == ([], None)


def test_a_percentage_from_zero_is_none_and_the_absolute_change_still_stands():
    changes, _ = deltas([{"orders.count": 7}], result([{"orders.count": 0}]), [COUNT])
    assert changes[0].percent is None
    assert changes[0].change == 7


def test_a_measure_that_appeared_between_refreshes_is_skipped_not_treated_as_zero():
    # The query changed, which makes the pair incomparable rather than a rise from nothing.
    now = result([{"orders.net_revenue": 10, "orders.count": 2}])
    was = result([{"orders.net_revenue": 8}])
    changes, _ = deltas(now.rows, was, [REVENUE, COUNT])
    assert [d.label for d in changes] == ["Revenue"]


def test_dimensions_are_not_differenced():
    now = result([{"orders.status": "paid", "orders.count": 2}])
    was = result([{"orders.status": "open", "orders.count": 1}])
    changes, _ = deltas(now.rows, was, [STATUS, COUNT])
    assert [d.column for d in changes] == ["orders.count"]
