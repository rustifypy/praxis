from analyst_agent.cube.schema import CubeQuery
from analyst_agent.cube.shape import Column
from analyst_agent.cube.visualize import MAX_SERIES
from analyst_agent.dashboards import tiles

BAR = {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}


def check(query: dict, chart: dict, catalog):
    return tiles.validate(query, chart, catalog)


def test_a_sound_tile_passes(catalog):
    result = check(
        {"measures": ["orders.net_revenue"], "dimensions": ["orders.status"]}, BAR, catalog
    )
    assert result.ok, result.errors


def test_a_tile_cannot_be_born_naming_a_member_that_does_not_exist(catalog):
    result = check(
        {"measures": ["orders.revenue"]}, {"type": "kpi", "y": ["orders.revenue"]}, catalog
    )
    assert not result.ok
    assert "does not exist" in result.errors[0]


def test_a_tile_cannot_plot_a_column_its_query_will_not_return(catalog):
    result = check(
        {"measures": ["orders.net_revenue"], "dimensions": ["orders.status"]},
        {"type": "bar", "x": "customers.country", "y": ["orders.net_revenue"]},
        catalog,
    )
    assert not result.ok
    assert any("not in this result" in e for e in result.errors)


def test_an_unknown_chart_type_is_rejected(catalog):
    result = check(
        {"measures": ["orders.count"]}, {"type": "sunburst", "y": ["orders.count"]}, catalog
    )
    assert not result.ok
    assert "chart type must be one of" in result.errors[0]


def test_a_table_tile_needs_no_axes(catalog):
    result = check(
        {"measures": ["orders.count"], "dimensions": ["orders.status", "customers.country"]},
        {"type": "table"},
        catalog,
    )
    assert result.ok, result.errors


def test_a_chart_the_renderer_cannot_draw_is_refused_rather_than_downgraded(catalog):
    # area / stacked_bar / hbar have no branch in ResultChart. Accepting one would store a
    # tile that renders as a different chart than it claims, which is worse than a refusal.
    # When the renderer grows a branch, that comes first and CHART_TYPES second.
    for kind in ("area", "stacked_bar", "hbar"):
        result = check(
            {
                "measures": ["orders.net_revenue"],
                "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month"}],
            },
            {"type": kind, "x": "orders.created_at.month", "y": ["orders.net_revenue"]},
            catalog,
        )
        assert not result.ok, kind
        assert "chart type must be one of" in result.errors[0]


def test_the_types_a_tile_accepts_are_the_types_the_renderer_draws(catalog):
    # One list, checked here so widening it without widening the renderer trips a test.
    assert tiles.CHART_TYPES == ("line", "bar", "kpi", "table")


def test_an_axis_that_is_a_measure_is_refused(catalog):
    result = check(
        {"measures": ["orders.net_revenue"], "dimensions": ["orders.status"]},
        {"type": "bar", "x": "orders.net_revenue", "y": ["orders.net_revenue"]},
        catalog,
    )
    assert not result.ok
    assert any("is a measure" in e for e in result.errors)


# --- the window a refresh will and will not move -----------------------------------


def test_a_fixed_window_warns_but_does_not_refuse(catalog):
    result = check(
        {
            "measures": ["orders.net_revenue"],
            "timeDimensions": [
                {
                    "dimension": "orders.created_at",
                    "granularity": "month",
                    "dateRange": ["2026-01-01", "2026-03-31"],
                }
            ],
        },
        {"type": "line", "x": "orders.created_at.month", "y": ["orders.net_revenue"]},
        catalog,
    )
    assert result.ok, result.errors
    assert any("refreshing will not move it" in w for w in result.warnings)


def test_a_relative_window_does_not_warn(catalog):
    result = check(
        {
            "measures": ["orders.net_revenue"],
            "timeDimensions": [
                {
                    "dimension": "orders.created_at",
                    "granularity": "month",
                    "dateRange": "last 6 months",
                }
            ],
        },
        {"type": "line", "x": "orders.created_at.month", "y": ["orders.net_revenue"]},
        catalog,
    )
    assert result.ok, result.errors
    assert not any("will not move" in w for w in result.warnings)


def test_the_grouped_time_dimension_is_the_one_a_date_control_would_target():
    query = CubeQuery.model_validate(
        {
            "measures": ["orders.count"],
            "timeDimensions": [
                {"dimension": "customers.signed_up_at", "dateRange": "last year"},
                {"dimension": "orders.created_at", "granularity": "month"},
            ],
        }
    )
    # The grouped one, not the first one: narrowing the other would change the numbers
    # without changing the picture.
    assert tiles.primary_time_member(query) == "orders.created_at"


def test_a_time_filtered_tile_with_no_axis_still_names_a_target():
    query = CubeQuery.model_validate(
        {
            "measures": ["orders.count"],
            "timeDimensions": [{"dimension": "orders.created_at", "dateRange": "last 30 days"}],
        }
    )
    assert tiles.primary_time_member(query) == "orders.created_at"


def test_a_tile_with_no_time_dimension_names_nothing():
    query = CubeQuery.model_validate({"measures": ["orders.count"]})
    assert tiles.primary_time_member(query) == ""


# --- the shape a chart was authored on, and the shape it is refreshed into ---------

STATUS = Column(
    id="orders.status", label="Status", kind="dimension", type="string", format="", align="left"
)
MONTH = Column(
    id="orders.created_at.month", label="Month", kind="time", type="time", format="", align="left"
)
REVENUE = Column(
    id="orders.net_revenue", label="Revenue", kind="measure", type="sum", format="", align="right"
)


def rows(n: int, key: str = "orders.status") -> list[dict]:
    return [{key: f"v{i}", "orders.net_revenue": i} for i in range(n)]


def test_a_chart_that_still_fits_is_not_degraded():
    assert tiles.outgrown(BAR, [STATUS, REVENUE], rows(12)) == ""


def test_a_bar_chart_that_outgrew_its_legible_width_is_degraded():
    reason = tiles.outgrown(BAR, [STATUS, REVENUE], rows(40))
    assert "bars" in reason


def test_a_series_that_outgrew_the_palette_is_degraded():
    spec = {
        "type": "line",
        "x": "orders.created_at.month",
        "y": ["orders.net_revenue"],
        "series": "orders.status",
    }

    def data(n: int) -> list[dict]:
        return [
            {
                "orders.created_at.month": "2026-01-01",
                "orders.status": f"s{i}",
                "orders.net_revenue": i,
            }
            for i in range(n)
        ]

    # The cap is the palette's slot count, which never cycles — one constant for the
    # transcript and the dashboard both, because there is one palette.
    assert tiles.outgrown(spec, [MONTH, STATUS, REVENUE], data(MAX_SERIES)) == ""
    assert "series" in tiles.outgrown(spec, [MONTH, STATUS, REVENUE], data(MAX_SERIES + 1))


def test_a_result_past_the_chartable_row_count_is_degraded():
    spec = {"type": "line", "x": "orders.created_at.month", "y": ["orders.net_revenue"]}
    reason = tiles.outgrown(spec, [MONTH, REVENUE], rows(500, "orders.created_at.month"))
    assert "prefix of the data" in reason


def test_a_kpi_is_degraded_when_its_query_stops_returning_one_row():
    spec = {"type": "kpi", "y": ["orders.net_revenue"]}
    assert tiles.outgrown(spec, [REVENUE], [{"orders.net_revenue": 1}]) == ""
    assert "single row" in tiles.outgrown(spec, [REVENUE], rows(3))


def test_a_table_never_outgrows_its_shape():
    assert tiles.outgrown({"type": "table"}, [STATUS, REVENUE], rows(4000)) == ""


# --- what an insight model is allowed to read -------------------------------------


def test_a_small_result_is_handed_over_whole():
    data = rows(10)
    assert tiles.insight_slice([STATUS, REVENUE], data, budget=30) == data


def test_an_ordered_category_is_sliced_from_the_top():
    sliced = tiles.insight_slice([STATUS, REVENUE], rows(300), budget=30)
    assert len(sliced) == 30
    assert sliced[0]["orders.status"] == "v0"


def test_a_time_series_is_sampled_across_its_whole_span_not_truncated():
    data = rows(400, "orders.created_at.month")
    sliced = tiles.insight_slice([MONTH, REVENUE], data, budget=30)

    assert len(sliced) <= 31
    # The bug this exists to prevent: a head slice describes a trend from the oldest
    # third of the series. Both ends have to be present.
    assert sliced[0] is data[0]
    assert sliced[-1] is data[-1]


def test_the_final_bucket_of_a_time_series_is_never_dropped():
    # 31 rows against a budget of 10 gives a stride of 4, which lands on row 28 and would
    # otherwise lose "how did it end" — the one point an insight is most likely to need.
    data = rows(31, "orders.created_at.month")
    sliced = tiles.insight_slice([MONTH, REVENUE], data, budget=10)
    assert sliced[-1] is data[-1]


def test_outgrown_is_a_pure_function_of_the_stored_result():
    # The refresh path and the page-read path both call this with (chart, columns, rows), so
    # a reload reaches the same verdict as the refresh did. `degraded` is not persisted —
    # `tile_results` cannot gain a column — and a reload that skipped this drew the chart.
    spec = {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}
    columns, data = [STATUS, REVENUE], rows(40)
    assert tiles.outgrown(spec, columns, data) == tiles.outgrown(spec, columns, data) != ""
