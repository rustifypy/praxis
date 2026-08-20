import pytest

from model_store import repo

CUBE = "cubes:\n  - name: invoice\n"
VIEW = "views:\n  - name: sales\n"


@pytest.fixture
async def root(tmp_path):
    path = tmp_path / "model.git"
    await repo.ensure(path)
    return path


async def test_ensure_is_idempotent_and_leaves_one_ref(root):
    before = await repo.version(root, repo.MAIN)
    await repo.ensure(root)
    assert await repo.version(root, repo.MAIN) == before
    assert [r["name"] for r in await repo.refs(root)] == [repo.MAIN]


async def test_a_grounding_yml_is_refused_because_cube_compiles_every_yml(root):
    with pytest.raises(repo.RepoError):
        await repo.commit_files(
            root, repo.MAIN, {"grounding/probe.yml": "name: x\n"}, [], "m", replace=True
        )
    assert await repo.commit_files(
        root, repo.MAIN, {"grounding/rules/a.md": "# rule\n"}, [], "m", replace=False
    )


@pytest.mark.parametrize(
    "path",
    ["../escape.yml", "/abs.yml", "model.yml", "cubes/nested/../../x.yml", " cubes/a.yml"],
)
async def test_paths_outside_the_model_are_refused(root, path):
    with pytest.raises(repo.RepoError):
        await repo.commit_files(root, repo.MAIN, {path: CUBE}, [], "m", replace=True)


async def test_invalid_yaml_never_reaches_a_ref(root):
    with pytest.raises(repo.RepoError):
        await repo.commit_files(
            root, repo.MAIN, {"cubes/bad.yml": "cubes:\n  - name: [oops\n"}, [], "m", replace=True
        )
    assert await repo.files(root, repo.MAIN) == {}


async def test_replace_is_the_whole_tree_and_patch_keeps_the_rest(root):
    await repo.commit_files(
        root, repo.MAIN, {"cubes/invoice.yml": CUBE, "views/sales.yml": VIEW}, [], "m", replace=True
    )
    await repo.commit_files(root, repo.MAIN, {"views/churn.yml": VIEW}, [], "m", replace=False)
    assert sorted(await repo.files(root, repo.MAIN)) == [
        "cubes/invoice.yml",
        "views/churn.yml",
        "views/sales.yml",
    ]
    await repo.commit_files(root, repo.MAIN, {"cubes/invoice.yml": CUBE}, [], "m", replace=True)
    assert sorted(await repo.files(root, repo.MAIN)) == ["cubes/invoice.yml"]


async def test_a_delete_removes_only_what_it_names(root):
    await repo.commit_files(
        root, repo.MAIN, {"cubes/invoice.yml": CUBE, "views/sales.yml": VIEW}, [], "m", replace=True
    )
    await repo.commit_files(root, repo.MAIN, {}, ["views/sales.yml"], "m", replace=False)
    assert sorted(await repo.files(root, repo.MAIN)) == ["cubes/invoice.yml"]


async def test_an_unchanged_write_makes_no_commit(root):
    first = await repo.commit_files(root, repo.MAIN, {"cubes/a.yml": CUBE}, [], "m", replace=True)
    assert (
        await repo.commit_files(root, repo.MAIN, {"cubes/a.yml": CUBE}, [], "again", replace=True)
        == first
    )


async def test_refs_are_independent_so_a_branch_edit_leaves_main_alone(root):
    await repo.commit_files(root, repo.MAIN, {"cubes/a.yml": CUBE}, [], "m", replace=True)
    main_before = await repo.version(root, repo.MAIN)
    await repo.create_ref(root, "edit-1", repo.MAIN)
    await repo.commit_files(root, "edit-1", {"views/v.yml": VIEW}, [], "m", replace=False)

    assert await repo.version(root, repo.MAIN) == main_before
    assert sorted(await repo.files(root, "edit-1")) == ["cubes/a.yml", "views/v.yml"]
    assert sorted(await repo.files(root, repo.MAIN)) == ["cubes/a.yml"]


async def test_main_is_neither_deletable_nor_recreatable(root):
    with pytest.raises(repo.RepoError):
        await repo.delete_ref(root, repo.MAIN)
    with pytest.raises(repo.RepoError):
        await repo.create_ref(root, repo.MAIN, repo.MAIN)


async def test_a_ref_cannot_be_created_twice(root):
    await repo.create_ref(root, "edit-1", repo.MAIN)
    with pytest.raises(repo.RepoError):
        await repo.create_ref(root, "edit-1", repo.MAIN)


async def test_an_unknown_ref_is_a_404_rather_than_an_empty_model(root):
    with pytest.raises(repo.RepoError) as caught:
        await repo.files(root, "nope")
    assert caught.value.status == 404


async def test_diff_names_what_the_branch_did(root):
    await repo.commit_files(root, repo.MAIN, {"views/sales.yml": VIEW}, [], "m", replace=True)
    await repo.create_ref(root, "edit-1", repo.MAIN)
    await repo.commit_files(
        root, "edit-1", {"views/churn.yml": VIEW}, ["views/sales.yml"], "m", replace=False
    )
    changed = (await repo.diff(root, repo.MAIN, "edit-1"))["changed"]
    assert sorted((c["status"], c["path"]) for c in changed) == [
        ("A", "views/churn.yml"),
        ("D", "views/sales.yml"),
    ]


async def test_a_merge_fast_forwards_the_target(root):
    await repo.create_ref(root, "edit-1", repo.MAIN)
    await repo.commit_files(root, "edit-1", {"cubes/a.yml": CUBE}, [], "m", replace=False)
    await repo.merge(root, "edit-1", repo.MAIN, "merge")
    assert sorted(await repo.files(root, repo.MAIN)) == ["cubes/a.yml"]


async def test_a_conflicting_merge_leaves_the_target_untouched(root):
    """A conflict must never land: markers in a *.yml are zero cubes, from a direction
    nobody would look for it."""
    await repo.commit_files(root, repo.MAIN, {"cubes/a.yml": CUBE}, [], "m", replace=True)
    for name, body in (("c1", "cubes:\n  - name: one\n"), ("c2", "cubes:\n  - name: two\n")):
        await repo.create_ref(root, name, repo.MAIN)
        await repo.commit_files(root, name, {"cubes/a.yml": body}, [], "m", replace=False)
    await repo.merge(root, "c1", repo.MAIN, "merge")

    before = await repo.version(root, repo.MAIN)
    with pytest.raises(repo.RepoError) as caught:
        await repo.merge(root, "c2", repo.MAIN, "merge")
    assert caught.value.status == 409
    assert await repo.version(root, repo.MAIN) == before
    assert "<<<<<<<" not in (await repo.files(root, repo.MAIN))["cubes/a.yml"]


async def test_a_merge_with_nothing_new_moves_nothing(root):
    await repo.create_ref(root, "edit-1", repo.MAIN)
    before = await repo.version(root, repo.MAIN)
    assert await repo.merge(root, "edit-1", repo.MAIN, "merge") == before
