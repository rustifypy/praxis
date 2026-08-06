def test_ai_context_captured(catalog):
    assert catalog.get("orders.net_revenue").ai_context == "default revenue measure"


def test_operators_derived_from_type(catalog):
    assert "contains" in catalog.get("orders.status").valid_operators
    assert "gt" not in catalog.get("orders.status").valid_operators
    assert "measureFilter" in catalog.get("orders.count").valid_operators
    assert "inDateRange" in catalog.get("orders.created_at").valid_operators


def test_granularities_from_meta(catalog):
    assert catalog.get("orders.created_at").granularities == ["month", "day"]


def test_only_views_are_views_and_they_are_matched_whole(catalog):
    assert [v.name for v in catalog.views] == ["revenue"]
    assert catalog.views_containing(["revenue.count", "revenue.status"]) == ["revenue"]
    assert catalog.views_containing(["orders.count", "customers.country"]) == []


def test_nearest_returns_candidates(catalog):
    assert "orders.net_revenue" in catalog.nearest("orders.netrevenue")
