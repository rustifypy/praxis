"""The edit gate — tiers 1-2, so no container and no model calls."""

from copy import deepcopy

import yaml

from praxis.modelstore.editing import edits

ALBUM = {
    "name": "album",
    "sql_table": "public.album",
    "measures": [{"name": "count", "type": "count"}],
    "dimensions": [
        {"name": "album_id", "sql": "album_id", "type": "number", "primary_key": True},
        {"name": "title", "sql": "title", "type": "string"},
    ],
    "joins": [
        {
            "name": "artist",
            "sql": "{CUBE}.artist_id = {artist}.artist_id",
            "relationship": "many_to_one",
        }
    ],
}
CATALOG = {
    "name": "catalog",
    "cubes": [{"join_path": "album", "includes": ["title", "count"]}],
}


def base() -> dict[str, str]:
    return {
        "cubes/album.yml": yaml.safe_dump({"cubes": [deepcopy(ALBUM)]}, sort_keys=False),
        "views/catalog.yml": yaml.safe_dump({"views": [deepcopy(CATALOG)]}, sort_keys=False),
    }


def with_cube(body: dict, files: dict[str, str] | None = None) -> dict[str, str]:
    """`base()` with album replaced by `body`."""
    out = files or base()
    out["cubes/album.yml"] = yaml.safe_dump({"cubes": [body]}, sort_keys=False)
    return out


# --- tier 1 -------------------------------------------------------------------


def test_unchanged_model_passes():
    assert edits.check(base(), base()).ok


def test_invalid_yaml_is_refused():
    result = edits.check(base(), {**base(), "cubes/album.yml": "cubes:\n- name: [\n"})
    assert not result.ok
    assert "not valid YAML" in result.errors[0]


def test_a_stray_top_level_key_is_refused():
    """The probe.yml disaster: Cube compiles every *.yml under the mount, and one file it
    refuses takes the whole source to zero cubes."""
    result = edits.check(base(), {**base(), "cubes/probe.yml": "name: a saved query\nquery: {}\n"})
    assert not result.ok
    assert "unexpected top-level key" in result.errors[0]
    assert "zero cubes" in result.errors[0]


def test_an_empty_file_is_not_an_error():
    assert edits.check(base(), {**base(), "cubes/.gitkeep.yml": ""}).ok


def test_an_entity_without_a_name_is_refused():
    result = edits.check(base(), {**base(), "cubes/x.yml": "cubes:\n- sql_table: public.x\n"})
    assert not result.ok
    assert "has no name" in result.errors[0]


def test_two_entities_of_one_name_are_refused():
    files = base()
    files["cubes/copy.yml"] = yaml.safe_dump({"cubes": [deepcopy(ALBUM)]}, sort_keys=False)
    result = edits.check(base(), files)
    assert not result.ok
    assert "defined twice" in result.errors[0]


def test_a_cube_may_move_between_files():
    files = base()
    files.pop("cubes/album.yml")
    files["cubes/moved.yml"] = yaml.safe_dump({"cubes": [deepcopy(ALBUM)]}, sort_keys=False)
    assert edits.check(base(), files).ok


# --- shape --------------------------------------------------------------------


def test_a_view_needs_a_cubes_list():
    files = base()
    files["views/catalog.yml"] = yaml.safe_dump({"views": [{"name": "catalog"}]}, sort_keys=False)
    result = edits.check(base(), files)
    assert not result.ok
    assert "needs a `cubes:` list" in result.errors[0]


def test_a_view_join_path_must_start_at_a_real_cube():
    files = base()
    files["views/catalog.yml"] = yaml.safe_dump(
        {"views": [{"name": "catalog", "cubes": [{"join_path": "ghost", "includes": "*"}]}]},
        sort_keys=False,
    )
    result = edits.check(base(), files)
    assert not result.ok
    assert "not a cube in this model" in result.errors[0]


# --- tier 2: cubes are append-only --------------------------------------------


def test_removing_a_cube_is_refused():
    files = base()
    files.pop("cubes/album.yml")
    result = edits.check(base(), files)
    assert not result.ok
    # The cause leads, not the view it orphaned.
    assert "cube 'album' was removed" in result.errors[0]


def test_renaming_a_cube_is_refused():
    result = edits.check(base(), with_cube({**deepcopy(ALBUM), "name": "albums"}))
    assert not result.ok
    assert "cube 'album' was removed" in result.errors[0]


def test_removing_a_measure_is_refused():
    body = deepcopy(ALBUM)
    body["measures"] = []
    result = edits.check(base(), with_cube(body))
    assert not result.ok
    assert "album.count was removed" in result.errors[0]


def test_renaming_a_member_is_refused():
    body = deepcopy(ALBUM)
    body["dimensions"][1]["name"] = "album_title"
    result = edits.check(base(), with_cube(body))
    assert not result.ok
    assert "album.title was removed" in result.errors[0]


def test_changing_a_members_sql_is_refused():
    body = deepcopy(ALBUM)
    body["dimensions"][1]["sql"] = "upper(title)"
    result = edits.check(base(), with_cube(body))
    assert not result.ok
    assert "`sql` changed" in result.errors[0]


def test_changing_the_table_is_refused():
    result = edits.check(base(), with_cube({**deepcopy(ALBUM), "sql_table": "public.other"}))
    assert not result.ok
    assert "`sql_table` changed" in result.errors[0]


def test_removing_a_join_is_refused():
    body = deepcopy(ALBUM)
    body.pop("joins")
    result = edits.check(base(), with_cube(body))
    assert not result.ok
    assert "the join to 'artist' was removed" in result.errors[0]


def test_documentation_may_be_edited():
    body = deepcopy(ALBUM)
    body["description"] = "One album."
    body["measures"][0]["description"] = "How many albums."
    body["measures"][0]["meta"] = {"ai_context": "count of albums, not tracks"}
    body["dimensions"][1]["title"] = "Album title"
    body["dimensions"][1]["format"] = "id"
    result = edits.check(base(), with_cube(body))
    assert result.ok, result.errors


def test_hiding_a_member_is_refused():
    """`public: false` is not a rename, but it leaves the queryable surface exactly as a
    deletion does."""
    body = deepcopy(ALBUM)
    body["measures"][0]["public"] = False
    result = edits.check(base(), with_cube(body))
    assert not result.ok
    assert "`public` was added" in result.errors[0]


def test_additions_are_allowed():
    body = deepcopy(ALBUM)
    body["measures"].append({"name": "titles", "type": "count_distinct", "sql": "title"})
    body["dimensions"].append({"name": "shouty", "sql": "upper({CUBE}.title)", "type": "string"})
    result = edits.check(base(), with_cube(body))
    assert result.ok, result.errors


def test_a_whole_new_cube_is_allowed():
    files = base()
    files["cubes/artist.yml"] = yaml.safe_dump(
        {
            "cubes": [
                {
                    "name": "artist",
                    "sql_table": "public.artist",
                    "measures": [{"name": "count", "type": "count"}],
                }
            ]
        },
        sort_keys=False,
    )
    assert edits.check(base(), files).ok


def test_views_may_be_renamed_removed_and_added():
    files = base()
    files.pop("views/catalog.yml")
    files["views/sales.yml"] = yaml.safe_dump(
        {"views": [{"name": "sales", "cubes": [{"join_path": "album", "includes": "*"}]}]},
        sort_keys=False,
    )
    assert edits.check(base(), files).ok


def test_a_member_the_branch_added_is_not_frozen():
    """The base is main, not the previous state: a measure that never reached main is the
    branch's own, so reshaping or dropping it before the merge is not a removal."""
    added = deepcopy(ALBUM)
    added["measures"].append({"name": "titles", "type": "count_distinct", "sql": "title"})
    branch = with_cube(added)
    assert edits.check(base(), branch).ok

    reshaped = deepcopy(added)
    reshaped["measures"][1]["type"] = "count"
    assert edits.check(base(), with_cube(reshaped)).ok

    assert edits.check(base(), with_cube(deepcopy(ALBUM))).ok


def test_an_unparseable_main_warns_rather_than_blocking():
    """A `cubes` step Cube rejected is committed to main on purpose, so this is a real state."""
    broken = {**base(), "cubes/album.yml": "cubes:\n- name: [\n"}
    result = edits.check(broken, base())
    assert result.ok
    assert "main does not fully parse" in result.warnings[0]


# --- the ambiguous-sql warning ------------------------------------------------


def test_unqualified_columns():
    assert edits.unqualified_columns("unit_price") == set()
    assert edits.unqualified_columns("{CUBE}.quantity * {CUBE}.unit_price") == set()
    assert edits.unqualified_columns("quantity * unit_price") == {"quantity", "unit_price"}
    # A function name is not a column, and a single column inside an expression still is.
    assert edits.unqualified_columns("SUM(quantity)") == {"quantity"}
    assert edits.unqualified_columns("SUM(a) / SUM(b)") == {"a", "b"}
    assert edits.unqualified_columns("CASE WHEN status IS NULL THEN 0 ELSE 1 END") == {"status"}
    assert edits.unqualified_columns("CONCAT('x', {CUBE}.title)") == set()


def test_an_added_measure_with_unqualified_sql_warns_but_passes():
    body = deepcopy(ALBUM)
    body["measures"].append({"name": "margin", "type": "sum", "sql": "price - cost"})
    result = edits.check(base(), with_cube(body))
    assert result.ok
    assert "cost, price are unqualified" in result.warnings[0]
    assert "{CUBE}.column" in result.warnings[0]


def test_a_qualified_added_measure_does_not_warn():
    body = deepcopy(ALBUM)
    body["measures"].append({"name": "margin", "type": "sum", "sql": "{CUBE}.price - {CUBE}.cost"})
    result = edits.check(base(), with_cube(body))
    assert result.ok
    assert result.warnings == []


def test_as_message_reads_as_instructions():
    result = edits.check(base(), with_cube({**deepcopy(ALBUM), "sql_table": "public.other"}))
    assert result.as_message().startswith("EDIT REJECTED:")
