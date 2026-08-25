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


def test_view_members_carry_the_cube_member_they_re_export(catalog):
    assert catalog.get("revenue.net_revenue").alias_member == "orders.net_revenue"
    assert catalog.get("revenue.created_at").alias_member == "orders.created_at"


def test_a_cubes_own_members_have_no_alias(catalog):
    """Which is what makes the field usable as "is this a projection or the real thing"."""
    assert catalog.get("orders.net_revenue").alias_member == ""
    assert catalog.get("customers.country").alias_member == ""


def test_alias_members_name_the_cubes_a_view_is_built_from(catalog):
    """The relationship the sources panel splits views from cubes on: `revenue` projects
    over `orders` alone, so `customers` is not reachable through it."""
    view = next(v for v in catalog.views if v.name == "revenue")
    assert {m.alias_member.rpartition(".")[0] for m in view.members} == {"orders"}


def test_nearest_returns_candidates(catalog):
    assert "orders.net_revenue" in catalog.nearest("orders.netrevenue")


def test_nearest_ranks_on_the_member_not_the_cube_prefix(catalog):
    """Scoring the whole id ranked by the shared `orders.` prefix, so an invented
    name came back with arbitrary members of the right cube and not the one meant."""
    assert catalog.nearest("orders.revenue")[0] == "orders.net_revenue"


def test_nearest_matches_across_an_underscore(catalog):
    """snake_case is tokenised, so a member named by one of its words is found."""
    assert "orders.created_at" in catalog.nearest("orders.created")


def test_nearest_is_empty_when_nothing_is_close(catalog):
    """Three misleading suggestions are worse than saying there is no near match."""
    assert catalog.nearest("orders.zzqqxx") == []


# --- the projection graph and model size ------------------------------------


def test_a_view_reports_the_cubes_it_projects_over(catalog):
    """`aliasMember` is the only thing in /v1/meta that says what a view is made of —
    without it a view and a cube differ by a type string."""
    revenue = next(e for e in catalog.entities if e.name == "revenue")
    assert catalog.draws_from(revenue) == ["orders"]
    assert catalog.exposed_by(revenue) == []


def test_a_cube_reports_the_views_exposing_it(catalog):
    orders = next(e for e in catalog.entities if e.name == "orders")
    assert catalog.exposed_by(orders) == ["revenue"]
    assert catalog.draws_from(orders) == []


def test_a_cube_no_view_reaches_is_exposed_by_nothing(catalog):
    """The actionable case: a cube reachable only by querying it directly."""
    customers = next(e for e in catalog.entities if e.name == "customers")
    assert catalog.exposed_by(customers) == []


def test_model_size_counts_cubes_only(catalog):
    """A view's members are the cube's members re-exported, so counting every entity
    reports the model as roughly twice its size."""
    counts = catalog.counts()
    assert (counts.cubes, counts.views) == (2, 1)
    # orders has 5, customers 2. The view's 4 are the same members again.
    assert counts.members == 7
    assert counts.members < sum(len(e.members) for e in catalog.entities)


def test_exposed_counts_members_some_view_re_exports(catalog):
    """Surrogate keys are left out of views on purpose, so this is never the whole
    membership of a healthy model."""
    counts = catalog.counts()
    assert counts.exposed == 4
    assert counts.exposed < counts.members


def test_counts_survive_a_model_with_no_views():
    from analyst_agent.modelstore.catalog import Catalog

    bare = Catalog.from_meta(
        {"cubes": [{"name": "orders", "type": "cube", "measures": [{"name": "orders.count"}]}]}
    )
    counts = bare.counts()
    assert (counts.cubes, counts.views, counts.members, counts.exposed) == (1, 0, 1, 0)
