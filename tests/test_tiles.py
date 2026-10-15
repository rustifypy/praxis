from praxis.modelstore import charts as tiles
from praxis.modelstore.charts import MAX_HBARS, MAX_SERIES, MAX_SLICES
from praxis.modelstore.schema import CubeQuery
from praxis.modelstore.shape import Column

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
    # These have no branch in ResultChart. Accepting one would store a tile that renders as a
    # different chart than it claims, which is worse than a refusal. When the renderer grows a
    # branch, that comes first and CHART_TYPES second.
    for kind in ("treemap", "waterfall", "funnel"):
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
    assert tiles.CHART_TYPES == (
        "line",
        "area",
        "stacked_area",
        "bar",
        "stacked_bar",
        "grouped_bar",
        "hbar",
        "donut",
        "scatter",
        "heatmap",
        "gauge",
        "sparkline",
        "kpi",
        "stat",
        "table",
    )


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


def test_a_stacked_bar_counts_bars_rather_than_rows():
    """It carries one row per series per bar, so a row count degrades a chart that is twenty
    legible columns wide. Caught against real data, where 20 quarters arrived as 47 rows."""
    spec = {
        "type": "stacked_bar",
        "x": "orders.created_at.month",
        "y": ["orders.net_revenue"],
        "series": "orders.status",
    }
    data = [
        {"orders.created_at.month": f"2026-{m:02d}-01", "orders.status": s, "orders.net_revenue": m}
        for m in range(1, 21)
        for s in ("paid", "refunded")
    ]
    assert len(data) == 40
    assert tiles.outgrown(spec, [MONTH, STATUS, REVENUE], data) == ""


def test_a_grouped_bar_counts_every_bar_it_draws():
    """Groups times series, because that is how many bars end up side by side."""
    spec = {
        "type": "grouped_bar",
        "x": "orders.created_at.month",
        "y": ["orders.net_revenue"],
        "series": "orders.status",
    }
    data = [
        {"orders.created_at.month": f"2026-{m:02d}-01", "orders.status": s, "orders.net_revenue": m}
        for m in range(1, 11)
        for s in ("paid", "refunded", "pending")
    ]
    assert "30 bars" in tiles.outgrown(spec, [MONTH, STATUS, REVENUE], data)


def test_a_gauge_is_degraded_when_its_query_stops_returning_one_row():
    spec = {"type": "gauge", "y": ["orders.net_revenue"], "target": 100}
    assert tiles.outgrown(spec, [REVENUE], [{"orders.net_revenue": 1}]) == ""
    assert "single row" in tiles.outgrown(spec, [REVENUE], rows(3))


def test_a_donut_is_degraded_past_the_slices_it_can_label():
    spec = {"type": "donut", "x": "orders.status", "y": ["orders.net_revenue"]}
    assert tiles.outgrown(spec, [STATUS, REVENUE], rows(MAX_SLICES)) == ""
    assert "slices" in tiles.outgrown(spec, [STATUS, REVENUE], rows(MAX_SLICES + 1))


def test_a_donut_refuses_a_negative_share_of_a_whole():
    """A negative slice has no arc: it would either vanish or eat one of its neighbours."""
    spec = {"type": "donut", "x": "orders.status", "y": ["orders.net_revenue"]}
    data = [
        {"orders.status": "a", "orders.net_revenue": 5},
        {"orders.status": "b", "orders.net_revenue": -2},
    ]
    assert "negative" in tiles.outgrown(spec, [STATUS, REVENUE], data)


def test_a_horizontal_bar_fits_more_rows_than_an_upright_one():
    """Labels run along the axis rather than under it, so they read at any length."""
    spec = {"type": "hbar", "x": "orders.status", "y": ["orders.net_revenue"]}
    assert "bars" in tiles.outgrown(BAR, [STATUS, REVENUE], rows(MAX_HBARS))
    assert tiles.outgrown(spec, [STATUS, REVENUE], rows(MAX_HBARS)) == ""
    assert "bars" in tiles.outgrown(spec, [STATUS, REVENUE], rows(MAX_HBARS + 1))


def test_a_table_never_outgrows_its_shape():
    assert tiles.outgrown({"type": "table"}, [STATUS, REVENUE], rows(4000)) == ""
