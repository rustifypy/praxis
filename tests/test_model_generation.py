"""The deterministic half of model generation — no containers, no model calls."""

import yaml

from analyst_agent.modelstore.modelgen import build, publish
from analyst_agent.modelstore.modelgen.decide import (
    CubeDecision,
    FolderDecision,
    MeasureDecision,
    MemberDescription,
    ViewCube,
    ViewDecision,
    exposed_members,
    qualify_expression,
    validate_cube,
    validate_paths,
    validate_views,
)
from analyst_agent.modelstore.modelgen.warehouse import Column, ForeignKey, Schema, Table


def col(name, data_type="integer", distinct=None, null_fraction=None):
    return Column(name=name, data_type=data_type, distinct=distinct, null_fraction=null_fraction)


def chinook() -> Schema:
    """The shape that broke Cube's own scaffolder: keys named <table>_id, one composite."""
    album = Table(
        name="album",
        columns=[col("album_id"), col("artist_id"), col("title", "character varying")],
        primary_key=["album_id"],
        foreign_keys=[ForeignKey(["artist_id"], "artist", ["artist_id"])],
    )
    artist = Table(
        name="artist",
        columns=[col("artist_id"), col("name", "character varying")],
        primary_key=["artist_id"],
    )
    employee = Table(
        name="employee",
        columns=[col("employee_id"), col("reports_to"), col("title", "character varying")],
        primary_key=["employee_id"],
        foreign_keys=[ForeignKey(["reports_to"], "employee", ["employee_id"])],
    )
    playlist_track = Table(
        name="playlist_track",
        columns=[col("playlist_id"), col("track_id")],
        primary_key=["playlist_id", "track_id"],
        foreign_keys=[ForeignKey(["track_id"], "album", ["album_id"])],
    )
    invoice_line = Table(
        name="invoice_line",
        columns=[
            col("invoice_line_id"),
            col("album_id"),
            col("unit_price", "numeric", distinct=2),
            col("quantity", "integer", distinct=1),
        ],
        primary_key=["invoice_line_id"],
        foreign_keys=[ForeignKey(["album_id"], "album", ["album_id"])],
    )
    return Schema(tables=[album, artist, employee, playlist_track, invoice_line])


# --- types and keys ---------------------------------------------------------


def test_warehouse_types_map_onto_cubes_four():
    assert col("title", "character varying").type == "string"
    assert col("price", "numeric").type == "number"
    assert col("created", "timestamp without time zone").type == "time"
    assert col("active", "boolean").type == "boolean"


def test_single_primary_key_is_marked_and_hidden():
    key = build.primary_key_dimension(chinook().table("album"))
    assert key == {
        "name": "album_id",
        "sql": "album_id",
        "type": "number",
        "primary_key": True,
        "public": False,
    }


def test_composite_primary_key_is_concatenated():
    key = build.primary_key_dimension(chinook().table("playlist_track"))
    assert key["primary_key"] is True
    assert key["name"] == "id"
    assert key["sql"] == "CONCAT({CUBE}.playlist_id, '-', {CUBE}.track_id)"


def test_table_without_primary_key_gets_none():
    assert build.primary_key_dimension(Table(name="log", columns=[col("msg")])) is None


# --- joins -----------------------------------------------------------------


def test_join_is_emitted_from_the_foreign_key():
    schema = chinook()
    skipped: list[str] = []
    joins = build.joins(schema.table("album"), schema, skipped)
    assert joins == [
        {
            "name": "artist",
            "sql": "{CUBE}.artist_id = {artist}.artist_id",
            "relationship": "many_to_one",
        }
    ]
    assert not skipped


def test_self_referencing_join_is_skipped():
    schema = chinook()
    skipped: list[str] = []
    assert build.joins(schema.table("employee"), schema, skipped) == []
    assert "self-referencing" in skipped[0]


def test_join_to_a_keyless_table_is_skipped():
    schema = Schema(
        tables=[
            Table(
                name="fact",
                columns=[col("fact_id"), col("dim_id")],
                primary_key=["fact_id"],
                foreign_keys=[ForeignKey(["dim_id"], "dim", ["dim_id"])],
            ),
            Table(name="dim", columns=[col("dim_id")]),
        ]
    )
    skipped: list[str] = []
    assert build.joins(schema.table("fact"), schema, skipped) == []
    assert "no primary key" in skipped[0]


def test_every_emitted_cube_with_joins_has_a_primary_key():
    """The failure Cube reported on the GUI-generated model, asserted away."""
    result = build.emit_cubes(chinook())
    for path, body in publish.build_files(result.bodies).items():
        cube = yaml.safe_load(body)["cubes"][0]
        if "joins" in cube:
            keys = [d for d in cube.get("dimensions", []) if d.get("primary_key")]
            assert len(keys) == 1, f"{path} has joins but {len(keys)} primary keys"


def test_key_columns_are_not_offered_as_dimensions():
    dims = {d["name"] for d in build.dimensions(chinook().table("album"))}
    assert dims == {"title"}


# --- the candidate menu ----------------------------------------------------


def test_only_non_key_numeric_columns_are_measure_candidates():
    found = build.measure_candidates(chinook())
    assert {(c.cube, c.column) for c in found} == {
        ("invoice_line", "unit_price"),
        ("invoice_line", "quantity"),
    }
    # n_distinct=1 means summing it is a row count in disguise; the prompt says so.
    assert next(c for c in found if c.column == "quantity").always_same_value


def test_fact_tables_are_ranked_before_dimension_like_ones():
    roots = build.view_roots(chinook())
    facts = [r.cube for r in roots if r.is_fact]
    others = [r.cube for r in roots if not r.is_fact]
    assert set(facts) == {"invoice_line", "playlist_track"}, "nothing references these"
    assert others == ["album"], "album is referenced, so it can fan out"
    assert roots[0].is_fact, "a fact table must be offered first"


def test_join_paths_walk_upward_only():
    schema = chinook()
    paths = build.join_paths(schema, schema.table("invoice_line"))
    assert set(paths) == {"invoice_line", "invoice_line.album", "invoice_line.album.artist"}


# --- guarding the model's decisions ---------------------------------------


def _menu(schema):
    return build.Menu(measures=build.measure_candidates(schema), roots=build.view_roots(schema))


def test_measure_over_an_unknown_column_is_rejected():
    schema = chinook()
    decision, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[MeasureDecision(column="margin", name="margin", aggregation="sum")],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert decision.measures == []
    assert rejected[0].startswith("[candidate]")


def test_bad_aggregation_is_rejected():
    schema = chinook()
    _, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[MeasureDecision(column="unit_price", name="p", aggregation="median")],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert rejected[0].startswith("[aggregation]")


def test_expression_referencing_a_foreign_column_is_rejected():
    schema = chinook()
    _, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[
                MeasureDecision(
                    column="unit_price",
                    name="revenue",
                    aggregation="sum",
                    expression="unit_price * discount",
                )
            ],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert rejected[0].startswith("[expression]")


def test_a_kept_expression_comes_out_qualified():
    """Cube passes an expression to SQL verbatim, so we must qualify it before writing."""
    schema = chinook()
    decision, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[
                MeasureDecision(
                    column="unit_price",
                    name="revenue",
                    aggregation="sum",
                    expression="quantity * unit_price",
                )
            ],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert rejected == []
    assert decision.measures[0].expression == "{CUBE}.quantity * {CUBE}.unit_price"


def test_sql_functions_are_left_alone_when_qualifying():
    assert (
        qualify_expression("COALESCE(quantity, 0) * unit_price", {"quantity", "unit_price"})
        == "COALESCE({CUBE}.quantity, 0) * {CUBE}.unit_price"
    )
    assert qualify_expression("quantity * discount", {"quantity"}) is None


def test_measure_may_take_its_own_column_name():
    schema = chinook()
    decision, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[MeasureDecision(column="unit_price", name="unit_price", aggregation="avg")],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert [m.name for m in decision.measures] == ["unit_price"]
    assert rejected == []


def test_measure_may_not_take_another_columns_name():
    schema = chinook()
    decision, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[MeasureDecision(column="unit_price", name="quantity", aggregation="sum")],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert decision.measures == []
    assert rejected[0].startswith("[shadowing]")


def test_two_measures_with_one_name_keep_the_first():
    schema = chinook()
    decision, rejected = validate_cube(
        CubeDecision(
            description="",
            measures=[
                MeasureDecision(column="unit_price", name="revenue", aggregation="sum"),
                MeasureDecision(column="quantity", name="revenue", aggregation="sum"),
            ],
        ),
        schema.table("invoice_line"),
        _menu(schema),
    )
    assert [(m.name, m.column) for m in decision.measures] == [("revenue", "unit_price")]
    assert rejected[0].startswith("[duplicate]")


def test_documentation_for_unknown_members_is_dropped():
    schema = chinook()
    decision, _ = validate_cube(
        CubeDecision(description="", members=[MemberDescription(name="nope", description="x")]),
        schema.table("album"),
        _menu(schema),
    )
    assert decision.members == []


# --- views -----------------------------------------------------------------


def test_view_paths_must_descend_from_one_root():
    available = {"invoice_line": {"count"}, "album": {"title"}}
    view = ViewDecision(
        name="mixed",
        description="",
        ai_context="",
        cubes=[
            ViewCube(join_path="invoice_line", includes=["count"]),
            ViewCube(join_path="album", includes=["title"]),
        ],
    )
    kept, rejected = validate_paths(view, available)
    assert [e.join_path for e in kept] == ["invoice_line"]
    assert "does not descend from root" in rejected[0]


def test_joined_cubes_are_always_prefixed():
    available = {"invoice_line": {"count"}, "invoice_line.album": {"title"}}
    view = ViewDecision(
        name="sales",
        description="",
        ai_context="",
        cubes=[
            ViewCube(join_path="invoice_line", includes=["count"], prefix=True),
            ViewCube(join_path="invoice_line.album", includes=["title"], prefix=False),
        ],
    )
    kept, _ = validate_paths(view, available)
    assert [(e.join_path, e.prefix) for e in kept] == [
        ("invoice_line", False),
        ("invoice_line.album", True),
    ]


def test_the_same_cube_reached_twice_is_rejected():
    available = {
        "invoice_line": {"count"},
        "invoice_line.album": {"title"},
        "invoice_line.other.album": {"title"},
    }
    view = ViewDecision(
        name="dupe",
        description="",
        ai_context="",
        cubes=[
            ViewCube(join_path="invoice_line", includes=["count"]),
            ViewCube(join_path="invoice_line.album", includes=["title"]),
            ViewCube(join_path="invoice_line.other.album", includes=["title"]),
        ],
    )
    kept, rejected = validate_paths(view, available)
    assert len(kept) == 2
    assert "already reached" in rejected[-1]


def test_colliding_member_is_aliased_not_dropped():
    """Cube's own remedy for a name conflict is an alias, and it must reach the YAML."""
    view = ViewDecision(
        name="v",
        description="",
        ai_context="",
        cubes=[
            ViewCube(join_path="invoice_line", includes=["album_title"], prefix=False),
            ViewCube(join_path="invoice_line.album", includes=["title"], prefix=True),
        ],
    )
    assert [name for _, _, name, _ in exposed_members(view)] == [
        "album_title",
        "album_title_2",
    ]
    body = publish.view_body(view)
    assert body["cubes"][1]["includes"] == [{"name": "title", "alias": "album_title_2"}]


def test_folders_keep_only_exposed_members():
    available = {"invoice_line": {"count", "quantity"}}
    view = ViewDecision(
        name="v",
        description="",
        ai_context="",
        cubes=[ViewCube(join_path="invoice_line", includes=["count", "quantity"])],
        folders=[FolderDecision(name="Volume", includes=["count", "ghost"])],
    )
    kept, _ = validate_views([view], _fake_roots(available), {})
    assert kept[0].folders[0].includes == ["count"]


def _fake_roots(available):
    root = build.ViewRoot(cube="invoice_line", is_fact=True, paths={})
    for path, members in available.items():
        root.paths[path] = [m for m in members if m != "count"]
    return [root]


# --- applying decisions ----------------------------------------------------


def test_measure_supersedes_the_dimension_of_the_same_name():
    """Cube rejects two members with one name; the raw numeric dimension gives way."""
    schema = chinook()
    body = build.cube(schema.table("invoice_line"), schema, [])
    assert "unit_price" in {d["name"] for d in body["dimensions"]}

    applied = publish.apply_cube(
        body,
        CubeDecision(
            description="One sold line.",
            measures=[MeasureDecision(column="unit_price", name="unit_price", aggregation="avg")],
        ),
    )
    assert "unit_price" not in {d["name"] for d in applied["dimensions"]}
    assert "unit_price" in {m["name"] for m in applied["measures"]}


def test_primary_key_survives_a_measure_of_the_same_name():
    schema = chinook()
    body = build.cube(schema.table("invoice_line"), schema, [])
    applied = publish.apply_cube(
        body,
        CubeDecision(
            description="",
            measures=[
                MeasureDecision(
                    column="unit_price", name="invoice_line_id", aggregation="count_distinct"
                )
            ],
        ),
    )
    keys = [d for d in applied["dimensions"] if d.get("primary_key")]
    assert len(keys) == 1


def test_descriptions_and_ai_context_land_on_members():
    schema = chinook()
    body = build.cube(schema.table("album"), schema, [])
    applied = publish.apply_cube(
        body,
        CubeDecision(
            description="One album.",
            members=[
                MemberDescription(
                    name="title", description="Album title.", ai_context="Prefer this."
                )
            ],
        ),
    )
    assert applied["description"] == "One album."
    title = next(d for d in applied["dimensions"] if d["name"] == "title")
    assert title["description"] == "Album title."
    assert title["meta"]["ai_context"] == "Prefer this."


def test_measure_expression_is_written_as_sql():
    measure = MeasureDecision(
        column="unit_price",
        name="revenue",
        aggregation="sum",
        expression="{CUBE}.unit_price * {CUBE}.quantity",
        ai_context="Default for revenue.",
    )
    body = publish.measure_body(measure)
    assert body == {
        "name": "revenue",
        "type": "sum",
        "sql": "{CUBE}.unit_price * {CUBE}.quantity",
        "meta": {"ai_context": "Default for revenue."},
    }


def test_measure_without_an_expression_stays_a_bare_column():
    """What Cube's own generator writes. Cube resolves a single identifier itself."""
    body = publish.measure_body(
        MeasureDecision(column="unit_price", name="avg_price", aggregation="avg")
    )
    assert body["sql"] == "unit_price"


def test_single_column_sql_is_left_bare_like_cubes_own_generator():
    """Cube resolves a single identifier against this cube, so bare is safe and idiomatic.
    Multi-column SQL is the exception — Cube passes that through verbatim."""
    files = publish.build_files(build.emit_cubes(chinook()).bodies)
    for path, body in files.items():
        cube = yaml.safe_load(body)["cubes"][0]
        for dimension in cube.get("dimensions", []):
            sql, name = dimension["sql"], dimension["name"]
            if dimension.get("primary_key") and name == "id":
                assert "{CUBE}" in sql, f"{path}: synthesised key must qualify"
            else:
                assert sql == name, f"{path}: {name} should be a bare column, got {sql!r}"
