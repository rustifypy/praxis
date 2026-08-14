from analyst_agent.cube.shape import columns_of
from analyst_agent.cube.visualize import MAX_BARS, MAX_SERIES, chart_for, validate_spec

MONTH = {"dimension": "orders.created_at", "granularity": "month"}


def months(n: int) -> list[dict]:
    return [{"orders.created_at.month": f"2024-{i + 1:02d}-01T00:00:00.000"} for i in range(n)]


def statuses(n: int) -> list[dict]:
    return [{"orders.status": f"s{i}", "orders.count": i} for i in range(n)]


# ---------- what gets a chart ----------


def test_a_measure_over_a_time_grain_is_a_line(catalog):
    query = {"measures": ["orders.net_revenue"], "timeDimensions": [MONTH]}
    spec = chart_for(query, catalog, months(12))
    assert (spec.type, spec.x, spec.y, spec.series) == (
        "line",
        "orders.created_at.month",
        ["orders.net_revenue"],
        "",
    )


def test_a_measure_by_a_dimension_is_a_bar(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec = chart_for(query, catalog, statuses(4))
    assert (spec.type, spec.x, spec.y) == ("bar", "orders.status", ["orders.count"])


def test_a_measure_with_no_grouping_is_a_kpi(catalog):
    query = {"measures": ["orders.count", "orders.net_revenue"]}
    spec = chart_for(query, catalog, [{"orders.count": 7, "orders.net_revenue": 12}])
    assert (spec.type, spec.x) == ("kpi", "")
    assert spec.y == ["orders.count", "orders.net_revenue"]


def test_a_dimension_over_time_becomes_one_line_per_value(catalog):
    query = {
        "measures": ["orders.net_revenue"],
        "dimensions": ["orders.status"],
        "timeDimensions": [MONTH],
    }
    rows = [
        {"orders.created_at.month": "2024-01-01", "orders.status": s} for s in ("paid", "refunded")
    ]
    spec = chart_for(query, catalog, rows)
    assert (spec.type, spec.series) == ("line", "orders.status")


def test_a_single_series_value_needs_no_legend(catalog):
    """One distinct value is one line, and a legend of one is clutter."""
    query = {
        "measures": ["orders.net_revenue"],
        "dimensions": ["orders.status"],
        "timeDimensions": [MONTH],
    }
    rows = [{"orders.created_at.month": f"2024-0{i}-01", "orders.status": "paid"} for i in (1, 2)]
    assert chart_for(query, catalog, rows).series == ""


def test_only_the_first_measure_is_plotted(catalog):
    """Revenue in thousands beside a count in tens flattens the count onto zero."""
    query = {
        "measures": ["orders.net_revenue", "orders.count"],
        "timeDimensions": [MONTH],
    }
    assert chart_for(query, catalog, months(6)).y == ["orders.net_revenue"]


# ---------- what correctly gets nothing ----------


def test_too_many_bars_is_a_table(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    assert chart_for(query, catalog, statuses(MAX_BARS)) is not None
    assert chart_for(query, catalog, statuses(MAX_BARS + 1)) is None


def test_too_many_series_is_a_table(catalog):
    query = {
        "measures": ["orders.net_revenue"],
        "dimensions": ["orders.status"],
        "timeDimensions": [MONTH],
    }
    rows = [{"orders.created_at.month": "2024-01-01", "orders.status": f"s{i}"} for i in range(20)]
    assert chart_for(query, catalog, rows) is None
    fewer = rows[:MAX_SERIES]
    assert chart_for(query, catalog, fewer) is not None


def test_two_dimensions_crossed_is_a_table(catalog):
    """A chart of one of them would silently hide the other."""
    query = {
        "measures": ["orders.count"],
        "dimensions": ["orders.status", "customers.country"],
    }
    assert chart_for(query, catalog, statuses(4)) is None


def test_two_time_axes_is_a_table(catalog):
    query = {
        "measures": ["orders.count"],
        "timeDimensions": [MONTH, {"dimension": "orders.created_at", "granularity": "day"}],
    }
    assert chart_for(query, catalog, months(3)) is None


def test_no_measure_is_nothing_to_plot(catalog):
    query = {"dimensions": ["orders.status"]}
    assert chart_for(query, catalog, statuses(3)) is None


def test_no_rows_is_nothing_to_plot(catalog):
    query = {"measures": ["orders.count"], "timeDimensions": [MONTH]}
    assert chart_for(query, catalog, []) is None


def test_an_ungrouped_multi_row_result_is_not_a_kpi(catalog):
    """A tile showing row one of several would be a quiet lie about the whole result."""
    query = {"measures": ["orders.count"]}
    assert chart_for(query, catalog, [{"orders.count": 1}, {"orders.count": 2}]) is None


def test_a_bare_date_range_leaves_no_axis_to_plot_against(catalog):
    """No granularity means no time column, so this is a single-row KPI, not a line."""
    query = {
        "measures": ["orders.count"],
        "timeDimensions": [{"dimension": "orders.created_at", "dateRange": "this year"}],
    }
    assert chart_for(query, catalog, [{"orders.count": 9}]).type == "kpi"


# ---------- the agent's override, checked like a query ----------


def columns(query, catalog):
    return columns_of(query, catalog)


def test_a_valid_override_passes(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "bar", "x": "orders.status", "y": ["orders.count"]}, columns(query, catalog)
    )
    assert errors == []
    assert spec.type == "bar"


def test_a_column_not_in_the_result_is_rejected(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]},
        columns(query, catalog),
    )
    assert spec is None
    assert "not in this result" in errors[0]


def test_a_dimension_on_the_y_axis_is_rejected(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "bar", "x": "orders.status", "y": ["orders.status"]}, columns(query, catalog)
    )
    assert spec is None
    assert "is a dimension, not a measure" in errors[0]


def test_a_measure_on_the_x_axis_is_rejected(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "bar", "x": "orders.count", "y": ["orders.count"]}, columns(query, catalog)
    )
    assert spec is None
    assert "is a measure" in errors[0]


def test_an_unknown_chart_type_is_rejected(catalog):
    query = {"measures": ["orders.count"]}
    spec, errors = validate_spec({"type": "sankey", "y": ["orders.count"]}, columns(query, catalog))
    assert spec is None
    assert "'line', 'bar' or 'kpi'" in errors[0]


def test_y_as_a_bare_string_is_a_type_error_not_twelve_bad_columns(catalog):
    """Iterating the string gave one "column 'o' is not in this result" per character, so the
    agent read a shape mistake as twelve naming mistakes."""
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "bar", "x": "orders.status", "y": "orders.count"}, columns(query, catalog)
    )
    assert spec is None
    assert errors == ["y: input should be a valid list"]


def test_a_key_the_spec_does_not_have_is_rejected(catalog):
    """The agent gets told the key is wrong instead of a chart it did not ask for."""
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "bar", "x": "orders.status", "y": ["orders.count"], "colour": "red"},
        columns(query, catalog),
    )
    assert spec is None
    assert errors == ["colour: extra inputs are not permitted"]


def test_a_spec_serialises_the_way_the_event_carries_it(catalog):
    """`run_query` and `set_chart` both put this on the wire, and it is the only place the
    browser reads a chart from — a spec that cannot be dumped is a transcript with no chart."""
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, _ = validate_spec(
        {"type": "bar", "x": "orders.status", "y": ["orders.count"]}, columns(query, catalog)
    )
    assert spec.model_dump() == {
        "type": "bar",
        "y": ["orders.count"],
        "x": "orders.status",
        "series": "",
    }


def test_a_kpi_takes_no_axis(catalog):
    query = {"measures": ["orders.count"], "dimensions": ["orders.status"]}
    spec, errors = validate_spec(
        {"type": "kpi", "x": "orders.status", "y": ["orders.count"]}, columns(query, catalog)
    )
    assert spec is None
    assert "kpi takes no x column" in errors


def test_a_bar_without_an_axis_is_rejected(catalog):
    query = {"measures": ["orders.count"]}
    spec, errors = validate_spec({"type": "bar", "y": ["orders.count"]}, columns(query, catalog))
    assert spec is None
    assert "needs an x column" in errors[0]


def test_series_is_line_only(catalog):
    query = {
        "measures": ["orders.count"],
        "dimensions": ["orders.status"],
        "timeDimensions": [MONTH],
    }
    spec, errors = validate_spec(
        {
            "type": "bar",
            "x": "orders.created_at.month",
            "y": ["orders.count"],
            "series": "orders.status",
        },
        columns(query, catalog),
    )
    assert spec is None
    assert "only meaningful on a line chart" in errors[0]


def test_an_empty_y_is_rejected(catalog):
    query = {"measures": ["orders.count"]}
    spec, errors = validate_spec({"type": "kpi", "y": []}, columns(query, catalog))
    assert spec is None
    assert "y: list should have at least 1 item" in errors[0]
