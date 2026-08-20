"""The model directory as a git repo. Real git against a temp directory — deterministic,
no containers, no model calls."""

import pytest

from analyst_agent.datamodel import vcs


@pytest.fixture
async def repo(tmp_path):
    (tmp_path / "cubes").mkdir()
    (tmp_path / "cubes" / "album.yml").write_text("cubes:\n- name: album\n")
    await vcs.ensure(tmp_path)
    return tmp_path


async def test_ensure_creates_a_repo_with_a_head(tmp_path):
    assert not vcs.is_repo(tmp_path)
    assert (await vcs.ensure(tmp_path)).ok
    assert vcs.is_repo(tmp_path)
    # A HEAD even with nothing to commit: branching and merging both need one.
    assert len(await vcs.log(tmp_path)) == 1
    assert await vcs.current(tmp_path) == vcs.MAIN


async def test_ensure_is_idempotent(repo):
    before = await vcs.log(repo)
    assert (await vcs.ensure(repo)).ok
    assert await vcs.log(repo) == before


async def test_ensure_on_a_missing_directory_fails_rather_than_creating_one(tmp_path):
    missing = tmp_path / "nope"
    result = await vcs.ensure(missing)
    assert not result.ok
    assert not missing.exists()


async def test_ensure_captures_an_existing_model_as_the_initial_commit(repo):
    """An unversioned model directory: whatever is already on disk has to reach history
    before the rewrite that would otherwise lose it."""
    assert (await vcs.run(repo, "show", "HEAD:cubes/album.yml")).out.strip().startswith("cubes:")


def test_validate_branch():
    assert vcs.validate_branch("add-margin") is None
    assert vcs.validate_branch("") is not None
    assert "generated branch" in vcs.validate_branch("main")
    assert vcs.validate_branch("has spaces") is not None
    assert vcs.validate_branch("a..b") is not None
    assert vcs.validate_branch("taken", ["taken"]) is not None


async def test_branch_round_trip(repo):
    assert (await vcs.create(repo, "add-margin")).ok
    assert await vcs.current(repo) == "add-margin"
    assert set(await vcs.branches(repo)) == {vcs.MAIN, "add-margin"}

    assert (await vcs.checkout(repo, vcs.MAIN)).ok
    assert await vcs.current(repo) == vcs.MAIN


async def test_commit_on_a_clean_tree_succeeds_without_adding_one(repo):
    before = await vcs.log(repo)
    result = await vcs.commit(repo, "nothing changed")
    assert result.ok
    assert await vcs.log(repo) == before


async def test_a_branch_diff_is_what_the_branch_changed(repo):
    await vcs.create(repo, "add-view")
    (repo / "views").mkdir()
    (repo / "views" / "sales.yml").write_text("views:\n- name: sales\n")
    await vcs.commit(repo, "add the sales view")

    assert await vcs.changed(repo, vcs.MAIN, "add-view") == [("A", "views/sales.yml")]
    assert "sales" in await vcs.diff(repo, vcs.MAIN, "add-view")


async def test_merge_installs_the_branch_on_main(repo):
    await vcs.create(repo, "add-view")
    (repo / "views").mkdir()
    (repo / "views" / "sales.yml").write_text("views:\n- name: sales\n")
    await vcs.commit(repo, "add the sales view")

    await vcs.checkout(repo, vcs.MAIN)
    assert (await vcs.merge(repo, "add-view")).ok
    assert (repo / "views" / "sales.yml").exists()


async def test_a_conflicted_merge_aborts_and_leaves_no_markers(repo):
    """The one that matters. A `<<<<<<<` in a cube file takes the source to zero cubes the
    moment Cube reloads, and the working tree here *is* the live bind mount."""
    await vcs.create(repo, "rename-it")
    (repo / "cubes" / "album.yml").write_text("cubes:\n- name: album\n  description: from branch\n")
    await vcs.commit(repo, "describe album")

    await vcs.checkout(repo, vcs.MAIN)
    (repo / "cubes" / "album.yml").write_text("cubes:\n- name: album\n  description: from main\n")
    await vcs.commit(repo, "describe album differently")

    result = await vcs.merge(repo, "rename-it")
    assert not result.ok
    body = (repo / "cubes" / "album.yml").read_text()
    assert "<<<<<<<" not in body
    assert body == "cubes:\n- name: album\n  description: from main\n"
    # Aborted, not left mid-merge: the next checkout has to work.
    assert await vcs.status(repo) == []


async def test_merge_of_an_unknown_branch_fails_without_wedging_the_tree(repo):
    assert not (await vcs.merge(repo, "no-such-branch")).ok
    assert await vcs.status(repo) == []
    assert (await vcs.checkout(repo, vcs.MAIN)).ok


async def test_status_reports_a_hand_edit(repo):
    (repo / "cubes" / "album.yml").write_text("cubes:\n- name: album\n  description: by hand\n")
    assert await vcs.status(repo) == ["cubes/album.yml"]
