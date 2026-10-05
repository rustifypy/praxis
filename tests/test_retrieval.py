"""What `search_model` can find. Lexical scoring is a judgement; which entities are in the pool
at all is a rule, and that rule is what is asserted here."""

from analyst_agent.agent.retrieval import LexicalRetriever


def test_a_cube_no_view_covers_can_still_be_found(catalog):
    """`customers` is a cube that no view re-exports. With a view present it used to be left out
    of the pool entirely, so no question could ever reach it."""
    found = [entity.name for entity in LexicalRetriever(catalog).search("customers by country")]
    assert "customers" in found


def test_a_cube_a_view_covers_stays_behind_the_view(catalog):
    """`orders` is re-exported by the `revenue` view, which is the governed way to ask about it."""
    found = [entity.name for entity in LexicalRetriever(catalog).search("orders", limit=10)]
    assert "revenue" in found
    assert "orders" not in found


def test_a_cube_with_one_member_no_view_carries_can_be_found(catalog):
    """A view that re-exports most of a cube still hides the column it left out — the case pack
    on an item dimension whose SKU a sales view kept."""
    from analyst_agent.modelstore.catalog import MemberDoc

    orders = next(cube for cube in catalog.cubes if cube.name == "orders")
    orders.members.append(
        MemberDoc(id="orders.case_pack", cube="orders", kind="dimension", type="number")
    )
    found = LexicalRetriever(catalog).search("orders case pack", limit=10)
    assert "orders" in [entity.name for entity in found]
