from analyst_agent.cube.catalog import Catalog
from analyst_agent.cube.describe import describe_query

META = {
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
    catalog = Catalog.from_meta(META)
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
