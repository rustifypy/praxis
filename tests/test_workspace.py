"""The one path both the HTTP routes and the editor agent take to change a model file.

Real git against a temp directory. The routes and tools on top are thin mappings from a
refusal to a status code or to prose, and are verified against a live source.
"""

import pytest

from analyst_agent.sources import registry
from analyst_agent.sources.edit import workspace
from analyst_agent.sources.model import vcs
from analyst_agent.sources.registry import Source

SOURCE = Source(name="shop", description="", driver="postgres", port=4001)

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


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "project_root", lambda: tmp_path)
    base = registry.scaffold_source_dirs(SOURCE, root=tmp_path)
    (base / "cubes" / "album.yml").write_text(ALBUM)
    (base / "grounding" / "rules" / "tone.md").write_text("# tone\n")
    return base


@pytest.fixture
async def branched(rooted):
    """Versioned and off main — the state every edit has to be in."""
    await vcs.ensure(rooted)
    await vcs.create(rooted, "edit")
    return rooted


# --- resolving a path --------------------------------------------------------


def test_a_model_path_resolves_inside_the_source(rooted):
    target, refused = workspace.resolve(SOURCE, "cubes/album.yml")
    assert target == (rooted / "cubes" / "album.yml").resolve()
    assert refused == ""


@pytest.mark.parametrize(
    "path",
    ["../../../../etc/passwd.yml", "/etc/passwd.yml", "cubes/../../escape.yml", ".git/config.yml"],
)
def test_a_path_leaving_the_model_directory_is_refused(rooted, path):
    """`..` and an absolute path both resolve out of the tree, so the check is on the resolved
    path: a check on the string itself lets `.git/hooks/x.yml` through."""
    target, refused = workspace.resolve(SOURCE, path)
    assert target is None
    assert "model directory" in refused


def test_only_yml_is_editable(rooted):
    """Cube ignores everything else under the mount, so writing it would be a silent no-op."""
    target, refused = workspace.resolve(SOURCE, "grounding/rules/tone.md")
    assert target is None
    assert "*.yml" in refused


def test_files_is_every_yml_and_nothing_else(rooted):
    (rooted / "views" / "sales.yml").write_text("views:\n- name: sales\n")
    assert sorted(workspace.files(SOURCE)) == ["cubes/album.yml", "views/sales.yml"]


# --- the guard ---------------------------------------------------------------


async def test_an_unversioned_model_is_versioned_before_anything_is_written(rooted):
    """The hole this module closes. `vcs.current` returns "" for a non-repo, which is not main,
    so the branch guard passed — and `files_at` returned nothing, so the append-only rules had
    no main to diff against and accepted a rewritten `sql_table`."""
    assert not vcs.is_repo(rooted)

    outcome = await workspace.write(SOURCE, "cubes/album.yml", ALBUM.replace("album", "album_v2"))

    assert not outcome.ok
    assert outcome.kind == "branch"
    assert vcs.is_repo(rooted)
    assert (rooted / "cubes" / "album.yml").read_text() == ALBUM


async def test_main_is_never_written_to(branched):
    await vcs.checkout(branched, vcs.MAIN)
    outcome = await workspace.write(SOURCE, "cubes/album.yml", ALBUM)
    assert (outcome.ok, outcome.kind) == (False, "branch")


async def test_the_guard_applies_to_deletes_too(branched):
    await vcs.checkout(branched, vcs.MAIN)
    outcome = await workspace.delete(SOURCE, "cubes/album.yml")
    assert (outcome.ok, outcome.kind) == (False, "branch")
    assert (branched / "cubes" / "album.yml").is_file()


# --- the gate, before the write ----------------------------------------------


async def test_a_valid_edit_reaches_disk(branched):
    added = ALBUM + "      - name: titles\n        type: count_distinct\n        sql: title\n"
    outcome = await workspace.write(SOURCE, "cubes/album.yml", added)
    assert outcome.ok, outcome.reason
    assert outcome.path == "cubes/album.yml"
    assert "titles" in (branched / "cubes" / "album.yml").read_text()


async def test_a_rewritten_sql_table_never_reaches_disk(branched):
    """Cubes are append-only against main. The write is live — Cube reloads in under a second —
    so refusing before `write_text` is what keeps the source up, not a rollback after."""
    outcome = await workspace.write(
        SOURCE, "cubes/album.yml", ALBUM.replace("public.album", "public.album_v2")
    )
    assert not outcome.ok
    assert outcome.kind == "rejected"
    assert (branched / "cubes" / "album.yml").read_text() == ALBUM


async def test_invalid_yaml_never_reaches_disk(branched):
    outcome = await workspace.write(SOURCE, "cubes/album.yml", "cubes: [oh: no: yes")
    assert not outcome.ok
    assert (branched / "cubes" / "album.yml").read_text() == ALBUM


async def test_deleting_a_cube_is_refused_and_the_file_survives(branched):
    outcome = await workspace.delete(SOURCE, "cubes/album.yml")
    assert not outcome.ok
    assert outcome.kind == "rejected"
    assert (branched / "cubes" / "album.yml").is_file()


async def test_a_view_may_be_deleted(branched):
    (branched / "views" / "sales.yml").write_text(
        "views:\n  - name: sales\n    cubes:\n      - join_path: album\n        includes:\n"
        "          - title\n"
    )
    outcome = await workspace.delete(SOURCE, "views/sales.yml")
    assert outcome.ok, outcome.reason
    assert not (branched / "views" / "sales.yml").exists()


async def test_deleting_what_is_not_there_says_so(branched):
    outcome = await workspace.delete(SOURCE, "views/nope.yml")
    assert (outcome.ok, outcome.kind) == (False, "missing")
