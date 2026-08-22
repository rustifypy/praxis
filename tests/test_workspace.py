"""The one path both the HTTP routes and the editor agent take to change a model file.

A fake store rather than a real one: what is being tested is that the gate runs before the
commit and that a refusal never reaches it. The routes and tools on top are thin mappings from a
refusal to a status code or to prose, and are verified against a live deployment.
"""

import pytest

from analyst_agent.core.deployment import MAIN
from analyst_agent.datamodel.editing import workspace

ALBUM = """cubes:
  - name: album
    sql_table: public.album
    dimensions:
      - name: title
        sql: title
        type: string
    measures:
      - name: count
        type: count
"""

SALES = """views:
  - name: sales
    cubes:
      - join_path: album
        includes:
          - title
"""


@pytest.fixture
def store(model_store):
    model = {"cubes/album.yml": ALBUM, "grounding/rules/tone.md": "# tone\n"}
    return model_store(main=model, edit=dict(model))


# --- resolving a path --------------------------------------------------------


def test_a_model_path_resolves_to_its_place_in_the_ref():
    relative, refused = workspace.resolve("cubes/album.yml")
    assert (relative, refused) == ("cubes/album.yml", "")


@pytest.mark.parametrize(
    "path",
    ["../../../../etc/passwd.yml", "/etc/passwd.yml", "cubes/../../escape.yml", ".git/config.yml"],
)
def test_a_path_leaving_the_model_is_refused(path):
    """A ref has no directory to escape from, but a path that tries is still not a model file —
    and the store would resolve it against its own tree."""
    relative, refused = workspace.resolve(path)
    assert relative == ""
    assert "cannot leave" in refused


def test_only_yml_is_editable():
    """Cube ignores everything else in the model, so writing it would be a silent no-op."""
    relative, refused = workspace.resolve("grounding/rules/tone.md")
    assert relative == ""
    assert "*.yml" in refused


def test_model_yaml_is_every_yml_and_nothing_else(store):
    store.refs["edit"]["views/sales.yml"] = SALES
    assert sorted(workspace.model_yaml(store.refs["edit"])) == [
        "cubes/album.yml",
        "views/sales.yml",
    ]


# --- naming a ref ------------------------------------------------------------


def test_main_is_not_a_name_an_edit_may_take():
    assert MAIN in workspace.validate_ref(MAIN)


@pytest.mark.parametrize("name", ["", "-leading", "has space", "trailing/", "dots..name"])
def test_an_unusable_ref_name_says_why(name):
    assert workspace.validate_ref(name)


def test_a_taken_name_is_refused():
    assert "already exists" in workspace.validate_ref("edit", ["main", "edit"])


# --- the guard ---------------------------------------------------------------


async def test_main_is_never_written_to(store):
    outcome = await workspace.write(store, MAIN, "cubes/album.yml", ALBUM)
    assert (outcome.ok, outcome.kind) == (False, "ref")
    assert store.commits == []


async def test_the_guard_applies_to_deletes_too(store):
    outcome = await workspace.delete(store, MAIN, "cubes/album.yml")
    assert (outcome.ok, outcome.kind) == (False, "ref")
    assert "cubes/album.yml" in store.refs[MAIN]


# --- the gate, before the commit ---------------------------------------------


async def test_a_valid_edit_is_committed(store):
    added = ALBUM + "      - name: titles\n        type: count_distinct\n        sql: title\n"
    outcome = await workspace.write(store, "edit", "cubes/album.yml", added)
    assert outcome.ok, outcome.reason
    assert outcome.path == "cubes/album.yml"
    assert "titles" in store.refs["edit"]["cubes/album.yml"]


async def test_a_rewritten_sql_table_is_never_committed(store):
    """Cubes are append-only against main. The write is the deploy — Cube picks the ref up within
    seconds — so refusing before the commit is what keeps it up, not a revert after."""
    outcome = await workspace.write(
        store, "edit", "cubes/album.yml", ALBUM.replace("public.album", "public.album_v2")
    )
    assert not outcome.ok
    assert outcome.kind == "rejected"
    assert store.refs["edit"]["cubes/album.yml"] == ALBUM
    assert store.commits == []


async def test_invalid_yaml_is_never_committed(store):
    outcome = await workspace.write(store, "edit", "cubes/album.yml", "cubes: [oh: no: yes")
    assert not outcome.ok
    assert store.refs["edit"]["cubes/album.yml"] == ALBUM


async def test_the_gate_diffs_against_main_and_not_the_refs_own_history(store):
    """Two edits in a row: the second is checked against main, so the first one's additions are
    not what freezes it. A member main carries stays frozen either way."""
    added = ALBUM + "      - name: titles\n        type: count_distinct\n        sql: title\n"
    assert (await workspace.write(store, "edit", "cubes/album.yml", added)).ok
    back = await workspace.write(store, "edit", "cubes/album.yml", ALBUM)
    assert back.ok, back.reason


async def test_deleting_a_cube_is_refused_and_the_file_survives(store):
    outcome = await workspace.delete(store, "edit", "cubes/album.yml")
    assert not outcome.ok
    assert outcome.kind == "rejected"
    assert "cubes/album.yml" in store.refs["edit"]


async def test_a_view_may_be_deleted(store):
    store.refs["edit"]["views/sales.yml"] = SALES
    outcome = await workspace.delete(store, "edit", "views/sales.yml")
    assert outcome.ok, outcome.reason
    assert "views/sales.yml" not in store.refs["edit"]


async def test_deleting_what_is_not_there_says_so(store):
    outcome = await workspace.delete(store, "edit", "views/nope.yml")
    assert (outcome.ok, outcome.kind) == (False, "missing")


async def test_a_store_that_does_not_answer_is_a_refusal_and_not_an_exception(store):
    """Both callers turn an Outcome into a status code or into prose for the agent; an exception
    escaping here would 500 the route and kill the turn."""
    store.fail = "the store did not answer"
    outcome = await workspace.write(store, "edit", "cubes/album.yml", ALBUM)
    assert (outcome.ok, outcome.kind) == (False, "store")
    assert "did not answer" in outcome.reason
