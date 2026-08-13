import pytest

from analyst_agent.cube import gate
from analyst_agent.cube.schema import CubeQuery


def check(payload: dict, catalog):
    q = CubeQuery.model_validate(payload)
    return gate.semantic_check(q, catalog)


def test_valid_query_passes(catalog):
    r = check(
        {
            "measures": ["revenue.net_revenue"],
            "timeDimensions": [
                {
                    "dimension": "revenue.created_at",
                    "granularity": "month",
                    "dateRange": ["2026-01-01", "2026-06-30"],
                }
            ],
            "order": {"revenue.created_at": "asc"},
        },
        catalog,
    )
    assert r.ok, r.errors


def test_unknown_member_suggests_alternatives(catalog):
    r = check({"measures": ["orders.total_revenue"]}, catalog)
    assert not r.ok
    assert "does not exist" in r.errors[0]
    assert "net_revenue" in r.errors[0]


def test_measure_used_as_dimension(catalog):
    r = check({"dimensions": ["orders.count"], "measures": ["orders.net_revenue"]}, catalog)
    assert not r.ok
    assert any("is a measure, not a dimension" in e for e in r.errors)


def test_bad_operator_for_type(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "filters": [{"member": "orders.status", "operator": "gt", "values": ["5"]}],
        },
        catalog,
    )
    assert not r.ok
    assert "invalid for orders.status" in r.errors[0]


def test_mixed_where_having_group_rejected(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "filters": [
                {
                    "and": [
                        {"member": "orders.status", "operator": "equals", "values": ["paid"]},
                        {"member": "orders.net_revenue", "operator": "gt", "values": ["100"]},
                    ]
                }
            ],
        },
        catalog,
    )
    assert not r.ok
    assert "WHERE vs HAVING" in r.errors[0]


def test_separate_top_level_filters_allowed(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "filters": [
                {"member": "orders.status", "operator": "equals", "values": ["paid"]},
                {"member": "orders.net_revenue", "operator": "gt", "values": ["100"]},
            ],
        },
        catalog,
    )
    assert r.ok, r.errors


def test_invalid_granularity(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "timeDimensions": [{"dimension": "orders.created_at", "granularity": "week"}],
        },
        catalog,
    )
    assert not r.ok
    assert "granularity 'week' unavailable" in r.errors[0]


def test_non_time_dimension_in_time_dimensions(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "timeDimensions": [{"dimension": "orders.status"}],
        },
        catalog,
    )
    assert not r.ok
    assert "not time" in r.errors[0]


def test_cross_entity_members_warn(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "dimensions": ["customers.country"],
            "order": {"orders.count": "desc"},
        },
        catalog,
    )
    assert r.ok
    assert any("no single view" in w for w in r.warnings)


def test_indaterange_filter_warns(catalog):
    r = check(
        {
            "measures": ["orders.count"],
            "filters": [
                {
                    "member": "orders.created_at",
                    "operator": "inDateRange",
                    "values": ["2026-01-01", "2026-06-30"],
                }
            ],
            "order": {"orders.count": "desc"},
        },
        catalog,
    )
    assert r.ok
    assert any("blocks pre-aggregation" in w for w in r.warnings)


def test_limit_without_order_warns(catalog):
    r = check({"measures": ["orders.count"], "limit": 10}, catalog)
    assert any("without order" in w for w in r.warnings)


def test_tier1_errors_returned_not_raised():
    parsed, errors = gate.parse({"measures": "not-a-list"})
    assert parsed is None and errors


def test_no_limit_does_not_warn(catalog):
    """The schema used to default `limit`, so this warning reached the agent on
    every query it ever ran — and the prompt tells it to report warnings."""
    r = check({"measures": ["orders.count"]}, catalog)
    assert not any("without order" in w for w in r.warnings)


def test_missing_values_reports_only_the_filter_branch():
    """A filter is a Filter or a FilterGroup, so pydantic reports both branches'
    failures. Only the branch matching the input's shape helps the agent retry."""
    parsed, errors = gate.parse(
        {
            "measures": ["orders.count"],
            "filters": [{"member": "orders.status", "operator": "equals"}],
        }
    )
    assert parsed is None
    assert len(errors) == 1
    assert "requires values" in errors[0]
    assert "FilterGroup" not in errors[0] and "function-after[" not in errors[0]


# --- tiers 3 and 4, over a fake session -------------------------------------


async def test_check_compiles_and_executes_nothing(catalog, cube):
    """`check` exists apart from `run` so a dashboard can know a query is sound without
    reading any data — the whole reason the seam is where it is."""
    result = await gate.check({"measures": ["orders.count"]}, catalog, cube, max_rows=500)
    assert result.ok
    assert result.sql == "SELECT 1"
    assert result.rows is None
    assert len(cube.seen) == 1  # compiled once, loaded never
    assert cube.seen[0]["measures"] == ["orders.count"]


async def test_check_caps_the_limit_it_sends(catalog, cube):
    """The row ceiling is not optional, and an absent limit is capped too."""
    await gate.check(
        {"measures": ["orders.count"], "limit": 40_000, "order": {"orders.count": "desc"}},
        catalog,
        cube,
        max_rows=500,
    )
    assert cube.seen[0]["limit"] == 500


async def test_a_bad_member_never_reaches_cube(catalog, cube):
    result = await gate.check({"measures": ["orders.nope"]}, catalog, cube, max_rows=500)
    assert not result.ok
    assert cube.seen == []


async def test_check_reports_cubes_refusal_without_raising(catalog, cube):
    cube.refuse = "Member not found: orders.count"
    result = await gate.check({"measures": ["orders.count"]}, catalog, cube, max_rows=500)
    assert not result.ok
    assert "Member not found" in result.errors[0]


async def test_execute_carries_the_sql_and_warnings_checked_produced(catalog, cube):
    """Why it takes a GateResult rather than the query: tiers 2-3's work survives into the
    executed result instead of being compiled a second time."""
    cube.rows = [{"orders.count": 42}]
    cube.resolved_ranges = {"orders.created_at": ["2026-01-01", "2026-01-31"]}
    checked = await gate.check(
        {"measures": ["orders.count"], "limit": 40_000}, catalog, cube, max_rows=500
    )
    assert checked.warnings
    loaded = await gate.execute(checked, cube)
    assert loaded.ok
    assert loaded.rows == [{"orders.count": 42}]
    assert loaded.sql == "SELECT 1"
    assert loaded.warnings == checked.warnings
    assert loaded.resolved_ranges == cube.resolved_ranges


async def test_execute_refuses_a_result_that_did_not_pass_check(catalog, cube):
    """An ordering constraint in the interface: without the parsed query there is nothing
    to send, and re-parsing here would compile the payload twice."""
    refused = await gate.check({"measures": ["orders.nope"]}, catalog, cube, max_rows=500)
    with pytest.raises(ValueError, match="passed check"):
        await gate.execute(refused, cube)


async def test_a_stopped_container_fails_the_execution_rather_than_the_caller(catalog, cube):
    """`dashboards.run` marks the tile failed; nothing above it sees an exception."""
    checked = await gate.check({"measures": ["orders.count"]}, catalog, cube, max_rows=500)
    cube.down = "cube did not answer"
    result = await gate.execute(checked, cube)
    assert not result.ok
    assert "did not answer" in result.errors[0]
    assert result.sql == "SELECT 1"
