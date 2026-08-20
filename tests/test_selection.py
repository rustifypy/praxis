"""Table selection, and the proof that nothing downstream needs to know about it.

The claim these tests defend: a deselected table is indistinguishable from a table in
another schema, which `build` already handles — so selection is a filter on the Schema
and `build`/`decide`/`publish` are untouched.
"""

from analyst_agent.datamodel.modelgen import build, selection
from analyst_agent.datamodel.modelgen.warehouse import Column, ForeignKey, Schema, Table


def table(name, *, key=None, refs=(), columns=("name",)) -> Table:
    return Table(
        name=name,
        columns=[Column(name=c, data_type="varchar") for c in columns]
        + [Column(name=key, data_type="integer")] * bool(key)
        + [Column(name=f"{r}_id", data_type="integer") for r in refs],
        primary_key=[key] if key else [],
        foreign_keys=[
            ForeignKey(columns=[f"{r}_id"], ref_table=r, ref_columns=[f"{r}_id"]) for r in refs
        ],
    )


# invoice_line -> invoice -> customer, and invoice_line -> track. `staff` is unrelated,
# `log` has no primary key so nothing may join to it.
CHINOOK = Schema(
    tables=[
        table("invoice_line", key="invoice_line_id", refs=("invoice", "track")),
        table("invoice", key="invoice_id", refs=("customer",)),
        table("customer", key="customer_id"),
        table("track", key="track_id"),
        table("staff", key="staff_id"),
        table("log", columns=("message",)),
    ]
)


def test_closure_pulls_in_what_a_fact_table_references():
    """Picking the grain you care about should bring its dimensions, transitively."""
    assert selection.closure(CHINOOK, ["invoice_line"]) == {
        "invoice_line",
        "invoice",
        "track",
        "customer",
    }


def test_closure_of_a_leaf_is_itself():
    assert selection.closure(CHINOOK, ["customer"]) == {"customer"}


def test_closure_ignores_a_name_that_is_not_in_the_schema():
    assert selection.closure(CHINOOK, ["invoice", "nope"]) == {"invoice", "customer"}


def test_closure_does_not_follow_a_key_to_a_keyless_table():
    """Cube refuses a join to a cube with no primary key, so pulling it in adds nothing."""
    schema = Schema(tables=[table("event", key="event_id", refs=("log",)), table("log")])
    assert selection.closure(schema, ["event"]) == {"event"}


def test_cut_joins_names_the_edges_a_selection_gives_up():
    """Without this the user cannot tell why a view came out thin."""
    assert selection.cut_joins(CHINOOK, ["invoice_line", "invoice", "customer"]) == [
        "invoice_line -> track"
    ]


def test_a_closed_selection_cuts_nothing():
    chosen = selection.closure(CHINOOK, ["invoice_line"])
    assert selection.cut_joins(CHINOOK, chosen) == []


def test_filter_keeps_only_the_chosen_tables():
    filtered = selection.filter_schema(CHINOOK, ["invoice", "customer"])
    assert sorted(t.name for t in filtered.tables) == ["customer", "invoice"]


def test_filter_drops_keys_pointing_at_unselected_tables():
    """A dead key would keep hiding its column from the dimension list."""
    filtered = selection.filter_schema(CHINOOK, ["invoice_line", "invoice"])
    line = filtered.table("invoice_line")
    assert [fk.ref_table for fk in line.foreign_keys] == ["invoice"]
    # track_id stops being join plumbing and becomes a dimension again.
    assert "track_id" not in line.key_columns
    assert "track_id" in {d["name"] for d in build.dimensions(line)}


def test_the_filtered_schema_builds_cubes_with_only_the_surviving_joins():
    """The whole point: `build` needs no knowledge that a selection happened."""
    filtered = selection.filter_schema(CHINOOK, selection.closure(CHINOOK, ["invoice"]))
    emitted = build.emit_cubes(filtered)
    assert sorted(emitted.bodies) == ["customer", "invoice"]
    assert [j["name"] for j in emitted.bodies["invoice"]["joins"]] == ["customer"]
    assert "joins" not in emitted.bodies["customer"]


def test_view_roots_follow_the_selection():
    """`view_roots` recomputes what is a fact table from what is present."""
    filtered = selection.filter_schema(CHINOOK, ["invoice_line", "invoice"])
    roots = build.view_roots(filtered)
    assert [r.cube for r in roots] == ["invoice_line"]
    assert roots[0].is_fact
    assert sorted(roots[0].paths) == ["invoice_line", "invoice_line.invoice"]


def test_keyless_tables_are_named():
    assert selection.keyless(CHINOOK) == ["log"]


def test_describe_marks_facts_and_reports_shape():
    rows = {row["name"]: row for row in selection.describe(CHINOOK)}
    assert rows["invoice_line"]["fact"] is True
    assert rows["invoice_line"]["references"] == ["invoice", "track"]
    assert rows["customer"]["fact"] is False
    assert rows["log"]["primary_key"] == []
    assert rows["invoice"]["columns"] == len(CHINOOK.table("invoice").columns)
