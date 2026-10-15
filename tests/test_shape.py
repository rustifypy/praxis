from praxis.modelstore.catalog import Catalog
from praxis.modelstore.shape import Column, columns_of, describe_query, export_value

# `format` and `shortTitle` are what a table needs and conftest's META carries neither, so
# this file brings its own. Everything else about the shape is exercised on the shared one.
FORMATTED = {
    "cubes": [
        {
            "name": "sales",
            "type": "view",
            "measures": [
                {
                    "name": "sales.revenue",
                    "type": "sum",
                    "shortTitle": "Net revenue",
                    "format": "currency",
                },
                {"name": "sales.margin", "type": "number", "format": "percent"},
            ],
            "dimensions": [
                {"name": "sales.country", "type": "string", "shortTitle": "Country"},
                {"name": "sales.invoiced_at", "type": "time", "granularities": [{"name": "month"}]},
                {"name": "sales.signed_up_at", "type": "time"},
            ],
        }
    ]
}


def formatted() -> Catalog:
    return Catalog.from_meta(FORMATTED)


def ids(query, catalog):
    return [c.id for c in columns_of(query, catalog)]


def test_labels_before_numbers(catalog):
    """A table reads left to right as labels then values, which is not the query's order."""
    query = {
        "measures": ["orders.net_revenue"],
        "dimensions": ["orders.status"],
    }
    assert ids(query, catalog) == ["orders.status", "orders.net_revenue"]


def test_the_time_axis_comes_first():
    query = {
        "measures": ["sales.revenue"],
        "dimensions": ["sales.country"],
        "timeDimensions": [{"dimension": "sales.invoiced_at", "granularity": "month"}],
    }
    columns = columns_of(query, formatted())
    assert [c.id for c in columns] == [
        "sales.invoiced_at.month",
        "sales.country",
        "sales.revenue",
    ]
    assert [c.kind for c in columns] == ["time", "dimension", "measure"]


def test_a_grouped_time_dimension_is_keyed_by_member_and_grain():
    """Cube appends the granularity to the column key; the member id alone finds nothing."""
    query = {
        "measures": ["sales.revenue"],
        "timeDimensions": [{"dimension": "sales.invoiced_at", "granularity": "month"}],
    }
    assert ids(query, formatted())[0] == "sales.invoiced_at.month"


def test_a_bare_date_range_adds_no_column():
    """Without a granularity a timeDimension narrows the result, it does not group it."""
    query = {
        "measures": ["sales.revenue"],
        "timeDimensions": [{"dimension": "sales.invoiced_at", "dateRange": "this year"}],
    }
    assert ids(query, formatted()) == ["sales.revenue"]


def test_one_time_axis_is_labelled_by_its_grain():
    query = {
        "measures": ["sales.revenue"],
        "timeDimensions": [{"dimension": "sales.invoiced_at", "granularity": "month"}],
    }
    assert columns_of(query, formatted())[0].label == "Month"


def test_two_time_axes_are_labelled_by_their_member():
    """Two grains of the same name on one row is a heading that says nothing."""
    query = {
        "measures": ["sales.revenue"],
        "timeDimensions": [
            {"dimension": "sales.invoiced_at", "granularity": "month"},
            {"dimension": "sales.signed_up_at", "granularity": "month"},
        ],
    }
    assert [c.label for c in columns_of(query, formatted())][:2] == [
        "invoiced at (month)",
        "signed up at (month)",
    ]


def test_measures_carry_cubes_own_format_and_align_right():
    query = {"measures": ["sales.revenue", "sales.margin"]}
    columns = columns_of(query, formatted())
    assert [(c.format, c.align) for c in columns] == [("currency", "right"), ("percent", "right")]


def test_a_headings_case_is_cubes_not_lowercased():
    """`_title` lowercases for mid-sentence use; a column heading must not."""
    query = {"measures": ["sales.revenue"], "dimensions": ["sales.country"]}
    assert [c.label for c in columns_of(query, formatted())] == ["Country", "Net revenue"]


def test_a_member_the_catalog_does_not_know_falls_back_to_its_name():
    """The gate rejects these before they run, so this is about never raising here."""
    empty = Catalog.from_meta({"cubes": []})
    columns = columns_of({"measures": ["orders.net_revenue"]}, empty)
    assert (columns[0].label, columns[0].type, columns[0].format) == ("net revenue", "", "")


def test_a_time_dimension_listed_as_a_plain_dimension_is_not_an_axis():
    """It has no grain, so `chart_for` must not find it — but it still formats as a date."""
    query = {"measures": ["sales.revenue"], "dimensions": ["sales.signed_up_at"]}
    column = columns_of(query, formatted())[0]
    assert (column.id, column.kind, column.type) == ("sales.signed_up_at", "dimension", "time")


def test_a_measure_only_query_is_all_right_aligned(catalog):
    query = {"measures": ["orders.count", "orders.net_revenue"]}
    assert {c.align for c in columns_of(query, catalog)} == {"right"}


def test_an_empty_query_has_no_columns(catalog):
    assert columns_of({}, catalog) == []


# ---------- export ----------


def time_column(grain: str) -> Column:
    return Column(
        id=f"sales.invoiced_at.{grain}",
        label=grain.title(),
        kind="time",
        type="time",
        format="",
        align="left",
    )


def test_export_trims_cubes_midnight_padding_from_a_date():
    """A column headed Year holding 2021-01-01T00:00:00.000 reads as a bug."""
    assert export_value("2021-01-01T00:00:00.000", time_column("year")) == "2021-01-01"
    assert export_value("2021-03-01T00:00:00.000", time_column("month")) == "2021-03-01"


def test_export_keeps_the_clock_when_the_grain_needs_it():
    """The midnight bucket of an hour grain is padded identically, so the grain decides."""
    assert export_value("2021-03-01T00:00:00.000", time_column("hour")) == (
        "2021-03-01T00:00:00.000"
    )


def test_export_leaves_numbers_raw(catalog):
    """A spreadsheet wants 12404.5, and grouping separators would make the column text."""
    column = columns_of({"measures": ["orders.net_revenue"]}, catalog)[0]
    assert export_value("12404.50", column) == "12404.50"


def test_export_renders_a_missing_cell_as_empty(catalog):
    column = columns_of({"measures": ["orders.net_revenue"]}, catalog)[0]
    assert export_value(None, column) == ""


# ---------- describe_query ----------


TITLED = {
    "cubes": [
        {
            "name": "sales_revenue",
            "type": "view",
            "measures": [{"name": "sales_revenue.net_total", "shortTitle": "Net revenue"}],
            "dimensions": [
                {
                    "name": "sales_revenue.billing_country",
                    "shortTitle": "Country",
                    "type": "string",
                },
                {"name": "sales_revenue.invoice_date", "type": "time"},
            ],
        }
    ]
}


def test_measure_and_dimension():
    catalog = Catalog.from_meta(TITLED)
    query = {
        "measures": ["sales_revenue.net_total"],
        "dimensions": ["sales_revenue.billing_country"],
    }
    assert describe_query(query, catalog) == "Net revenue by country"


def test_falls_back_to_the_column_name_without_a_catalog():
    query = {"measures": ["invoice_line.total_revenue"]}
    assert describe_query(query) == "Total revenue"


def test_granularity_groups_but_a_bare_date_range_only_narrows():
    grouped = {
        "measures": ["x.count"],
        "timeDimensions": [{"dimension": "x.d", "granularity": "month"}],
    }
    narrowed = {
        "measures": ["x.count"],
        "timeDimensions": [{"dimension": "x.d", "dateRange": "last quarter"}],
    }
    assert describe_query(grouped) == "Count by month"
    assert describe_query(narrowed) == "Count · last quarter"


def test_date_range_pair_is_rendered_as_a_span():
    query = {
        "measures": ["x.count"],
        "timeDimensions": [{"dimension": "x.d", "dateRange": ["2026-01-01", "2026-03-31"]}],
    }
    assert describe_query(query) == "Count · 2026-01-01 to 2026-03-31"


def test_ordered_limit_is_a_top_n_and_a_bare_limit_is_a_cap():
    base = {"measures": ["x.count"], "limit": 3}
    assert describe_query(base) == "Count · limit 3"
    assert describe_query({**base, "order": {"x.count": "desc"}}) == "Count · top 3"


def test_filters_are_counted_not_spelled_out():
    query = {"measures": ["x.count"], "filters": [{"member": "a"}, {"member": "b"}]}
    assert describe_query(query) == "Count · 2 filters"


def test_several_measures_read_as_a_list():
    query = {"measures": ["x.count", "x.total", "x.avg"]}
    assert describe_query(query) == "Count, total and avg"


def test_dimensions_only_query_still_gets_a_label():
    assert describe_query({"dimensions": ["x.name"]}) == "Name"


def test_empty_query_does_not_crash():
    assert describe_query({}) == "Query"
