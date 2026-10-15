"""The one path both the HTTP routes and the editor agent take to change a model file.

A fake store rather than a real one: what is being tested is that the gate runs before the
commit, that a refusal never reaches it, and that `verify` compiles a change set without putting
it on the ref being edited. The routes and tools on top are thin mappings from a refusal to a
status code or to prose, and are verified against a live deployment.

Cube is faked too. `verify`'s third tier is a real HTTP call to a real compiler, so what is
asserted here is the shape around it — which ref it asks about, that the ref is thrown away, and
that a refusal short-circuits before any of it.
"""

import pytest

from praxis.core.deployment import MAIN
from praxis.modelstore.editing import drafts, impact, workspace
from praxis.modelstore.modelgen import publish

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

# One more measure on the cube main already has: append-only, so this is the edit that passes.
ADDED = ALBUM + "      - name: titles\n        type: count_distinct\n        sql: title\n"


@pytest.fixture
def store(model_store):
    model = {"cubes/album.yml": ALBUM}
    return model_store(main=model, edit=dict(model))


@pytest.fixture
def compiling(monkeypatch, catalog):
    """Cube accepting everything, so `verify`'s own shape is what a test sees.

    Patched at `workspace`'s own names rather than deeper: these are the three calls it makes,
    and patching what it imported is what proves it called them at all.
    """

    async def check(ref=MAIN, **kwargs):
        return publish.Validation(compiled=True, entities=["album"])

    async def served(ref=MAIN, **kwargs):
        return catalog

    def broken(*args, **kwargs):
        return impact.Impact(checked=0)

    monkeypatch.setattr(workspace, "compiles", check)
    monkeypatch.setattr(workspace, "served_catalog", served)
    monkeypatch.setattr(workspace.impact, "check", broken)


async def published(store, ref, write=None, delete=None, message="edit model"):
    """Save into the draft, then publish it — which is what the routes do in two calls.

    A helper because `publish` no longer takes a change set: the draft *is* the change set, so a
    test that wants one has to put it on disk first.
    """
    await workspace.ensure(store, ref)
    for path, text in (write or {}).items():
        drafts.save(ref, path, text)
    for path in delete or []:
        drafts.remove(ref, path)
    return await workspace.publish(store, ref, message)


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


def test_a_file_cube_would_ignore_is_refused():
    relative, refused = workspace.resolve("cubes/album.txt")
    assert relative == ""
    assert "silent no-op" in refused


def test_model_yaml_is_every_yml_and_nothing_else(store):
    store.refs["edit"]["views/sales.yml"] = SALES
    assert sorted(workspace.model_yaml(store.refs["edit"])) == [
        "cubes/album.yml",
        "views/sales.yml",
    ]


# --- naming a ref ------------------------------------------------------------


def test_main_is_not_a_name_an_edit_may_take():
    assert MAIN in workspace.validate_ref(MAIN)


def test_the_preview_ref_is_not_a_name_an_edit_may_take():
    """`verify` deletes and recreates it under whatever is being edited, so a branch holding the
    name would be destroyed by the next Save."""
    assert workspace.validate_ref(workspace.PREVIEW)


@pytest.mark.parametrize("name", ["", "-leading", "has space", "trailing/", "dots..name"])
def test_an_unusable_ref_name_says_why(name):
    assert workspace.validate_ref(name)


def test_a_taken_name_is_refused():
    assert "already exists" in workspace.validate_ref("edit", ["main", "edit"])


# --- the guard ---------------------------------------------------------------


async def test_main_is_never_committed_to(store):
    outcome = await published(store, MAIN, {"cubes/album.yml": ALBUM})
    assert (outcome.ok, outcome.kind) == (False, "ref")
    assert store.commits == []


async def test_the_guard_applies_to_deletes_too(store):
    outcome = await published(store, MAIN, delete=["cubes/album.yml"])
    assert (outcome.ok, outcome.kind) == (False, "ref")
    assert "cubes/album.yml" in store.refs[MAIN]


async def test_main_is_not_verifiable_either(store):
    """Verify is Save, and there is no Save on main. Refused before any ref is created."""
    verdict = await workspace.verify(store, MAIN, {"cubes/album.yml": ADDED}, saved=[])
    assert (verdict.ok, verdict.kind) == (False, "ref")
    assert store.made == []


# --- the gate, before the commit ---------------------------------------------


async def test_a_valid_edit_is_committed(store):
    outcome = await published(store, "edit", {"cubes/album.yml": ADDED})
    assert outcome.ok, outcome.reason
    assert outcome.write == {"cubes/album.yml": ADDED}
    assert "titles" in store.refs["edit"]["cubes/album.yml"]


async def test_a_whole_change_set_is_one_commit(store):
    """A change spanning two files is one change: five commits would make a reviewer's diff five
    diffs, and a half-applied set is what a per-file loop leaves behind on the third failure."""
    outcome = await published(
        store, "edit", {"cubes/album.yml": ADDED, "views/sales.yml": SALES}, message="add sales"
    )
    assert outcome.ok, outcome.reason
    assert store.commits == ["add sales"]


async def test_a_rewritten_sql_table_is_never_committed(store):
    """Cubes are append-only against main. The write is the deploy — Cube picks the ref up within
    seconds — so refusing before the commit is what keeps it up, not a revert after."""
    outcome = await published(
        store, "edit", {"cubes/album.yml": ALBUM.replace("public.album", "public.album_v2")}
    )
    assert not outcome.ok
    assert outcome.kind == "rejected"
    assert store.refs["edit"]["cubes/album.yml"] == ALBUM
    assert store.commits == []


async def test_invalid_yaml_is_never_committed(store):
    outcome = await published(store, "edit", {"cubes/album.yml": "cubes: [oh: no: yes"})
    assert not outcome.ok
    assert store.refs["edit"]["cubes/album.yml"] == ALBUM


async def test_the_gate_diffs_against_main_and_not_the_refs_own_history(store):
    """Two edits in a row: the second is checked against main, so the first one's additions are
    not what freezes it. A member main carries stays frozen either way."""
    assert (await published(store, "edit", {"cubes/album.yml": ADDED})).ok
    back = await published(store, "edit", {"cubes/album.yml": ALBUM})
    assert back.ok, back.reason


async def test_deleting_a_cube_is_refused_and_the_file_survives(store):
    outcome = await published(store, "edit", delete=["cubes/album.yml"])
    assert not outcome.ok
    assert outcome.kind == "rejected"
    assert "cubes/album.yml" in store.refs["edit"]


async def test_a_view_may_be_deleted(store):
    store.refs["edit"]["views/sales.yml"] = SALES
    outcome = await published(store, "edit", delete=["views/sales.yml"])
    assert outcome.ok, outcome.reason
    assert "views/sales.yml" not in store.refs["edit"]


async def test_deleting_what_is_not_there_says_so(store):
    """Asked of `stage` rather than of a publish: a draft cannot be dirty with the deletion of a
    file it never had, so the only caller that can name one is the agent proposing it."""
    await workspace.ensure(store, "edit")
    outcome = await workspace.stage(store, "edit", delete=["views/nope.yml"])
    assert (outcome.ok, outcome.kind) == (False, "missing")


async def test_a_store_that_does_not_answer_is_a_refusal_and_not_an_exception(store):
    """Both callers turn an Outcome into a status code or into prose for the agent; an exception
    escaping here would 500 the route and kill the turn."""
    await workspace.ensure(store, "edit")
    drafts.save("edit", "cubes/album.yml", ADDED)
    store.fail = "the store did not answer"
    outcome = await workspace.publish(store, "edit")
    assert (outcome.ok, outcome.kind) == (False, "store")
    assert "did not answer" in outcome.reason


# --- verify: tier 3 without committing ---------------------------------------


async def test_verify_compiles_on_a_throwaway_ref_and_leaves_the_branch_alone(store, compiling):
    verdict = await workspace.verify(store, "edit", {"cubes/album.yml": ADDED}, saved=[])
    assert (verdict.ok, verdict.compiled) == (True, True)
    # Created from the ref being edited, so what compiles is that ref plus the change set.
    assert store.made == [(workspace.PREVIEW, "edit")]
    assert store.dropped == [workspace.PREVIEW]
    assert workspace.PREVIEW not in store.refs
    # The whole point: Save does not deploy.
    assert store.refs["edit"]["cubes/album.yml"] == ALBUM


async def test_a_gate_refusal_never_reaches_cube(store, compiling):
    """Tiers 1-2 first, so a rejected change set costs no ref and no ten-second compile."""
    verdict = await workspace.verify(
        store,
        "edit",
        {"cubes/album.yml": ALBUM.replace("public.album", "public.album_v2")},
        saved=[],
    )
    assert (verdict.ok, verdict.kind) == (False, "rejected")
    assert store.made == []


async def test_an_empty_change_set_asks_about_the_ref_as_committed(store, compiling):
    """This is the Validate action: no edits, so the throwaway ref is a copy of the ref and the
    answer is about the ref itself."""
    verdict = await workspace.verify(store, "edit", saved=[])
    assert verdict.compiled
    assert store.made == [(workspace.PREVIEW, "edit")]


async def test_a_model_cube_refuses_is_not_ok_and_says_why(store, monkeypatch, compiling):
    async def refused(ref=MAIN, **kwargs):
        return publish.Validation(compiled=False, compile_error="Compile errors: no such member")

    monkeypatch.setattr(workspace, "compiles", refused)
    verdict = await workspace.verify(store, "edit", {"cubes/album.yml": ADDED}, saved=[])
    assert not verdict.ok
    assert "no such member" in verdict.compile_error
    # Cleaned up even on a refusal: the next Save creates it again from scratch.
    assert store.dropped == [workspace.PREVIEW]


async def test_a_stale_preview_ref_is_cleared_on_the_way_in(store, compiling):
    """A crashed verify leaves one behind. Compiling whatever it happens to contain would report
    someone else's change set as this one's verdict."""
    store.refs[workspace.PREVIEW] = {"cubes/album.yml": "cubes: [broken"}
    verdict = await workspace.verify(store, "edit", {"cubes/album.yml": ADDED}, saved=[])
    assert verdict.ok
    assert store.dropped == [workspace.PREVIEW, workspace.PREVIEW]


async def test_member_errors_do_not_fail_a_save(store, monkeypatch, compiling):
    """A model that compiles with a measure that fails at query time still beats one that will
    not compile, so this is reported and not refused."""

    async def with_errors(ref=MAIN, **kwargs):
        return publish.Validation(
            compiled=True, entities=["album"], member_errors={"album.titles": "ambiguous column"}
        )

    monkeypatch.setattr(workspace, "compiles", with_errors)
    verdict = await workspace.verify(store, "edit", {"cubes/album.yml": ADDED}, saved=[])
    assert verdict.ok
    assert verdict.member_errors == {"album.titles": "ambiguous column"}


# --- the draft and the ref it is a copy of -----------------------------------


async def test_deleting_a_ref_takes_its_working_copy_with_it(store):
    """The two must not come apart. A draft outliving its ref is what let a reused name
    resurrect the old one — see `test_a_reused_name_does_not_resurrect_the_old_draft`."""
    await workspace.ensure(store, "edit")
    drafts.save("edit", "views/sales.yml", SALES)
    assert drafts.exists("edit")

    await workspace.forget(store, "edit")
    assert not drafts.exists("edit")
    assert "edit" not in store.refs


async def test_a_reused_name_does_not_resurrect_the_old_draft(store):
    """`delete` then `create` gives a *different* ref wearing the same name. Before this, the
    draft left behind reported itself clean against an origin that no longer existed: the editor
    listed a file the ref did not have, and Publish wrote nothing and said it had succeeded."""
    outcome = await published(store, "edit", {"views/sales.yml": SALES})
    assert outcome.ok, outcome.reason
    assert "views/sales.yml" in store.refs["edit"]

    await workspace.forget(store, "edit")
    await store.create_ref("edit", MAIN)

    await workspace.ensure(store, "edit")
    assert "views/sales.yml" not in drafts.files("edit"), "the new ref never had this file"
    assert not drafts.changes("edit").dirty


async def test_a_clean_draft_re_checks_out_when_the_ref_has_moved(store):
    """Nothing to lose: a draft with no changes is a copy, not work. This is what keeps a ref
    that moved underneath one from being read through a stale copy."""
    await workspace.ensure(store, "edit")
    assert "views/sales.yml" not in drafts.files("edit")

    # The store moves without this machine doing it — another tab, or the pipeline.
    await store.edit("edit", write={"views/sales.yml": SALES}, message="elsewhere")

    await workspace.ensure(store, "edit")
    assert "views/sales.yml" in drafts.files("edit")


async def test_a_dirty_draft_is_left_alone_when_the_ref_has_moved(store):
    """Staleness is reported, never resolved here — re-checking out would discard the edit."""
    await workspace.ensure(store, "edit")
    drafts.save("edit", "cubes/album.yml", ADDED)

    await store.edit("edit", write={"views/sales.yml": SALES}, message="elsewhere")

    await workspace.ensure(store, "edit")
    assert "titles" in drafts.read("edit", "cubes/album.yml"), "the local edit survived"
    assert drafts.changes("edit").dirty


# --- landing on main ---------------------------------------------------------


async def test_a_ref_that_fast_forwards_just_lands(store):
    await workspace.ensure(store, "edit")
    assert await workspace.land(store, "edit") == "merged"


async def test_an_unpublished_draft_is_not_silently_left_behind(store):
    """Those bytes are on this machine only, so the store would merge without them — the same
    reason it refuses a dirty worktree of its own."""
    await workspace.ensure(store, "edit")
    drafts.save("edit", "cubes/album.yml", ADDED)
    with pytest.raises(workspace.StoreError, match="unpublished changes"):
        await workspace.land(store, "edit")


async def test_a_ref_behind_main_is_rebased_and_then_lands(store, monkeypatch):
    """Fast-forward only, so a moved main is the ordinary case rather than an error."""
    calls: list[str] = []
    merges = iter([workspace.StoreError("not a fast-forward", status=409), None])

    async def merge(ref, into=MAIN):
        calls.append("merge")
        if (answer := next(merges)) is not None:
            raise answer

    async def rebase(ref):
        calls.append("rebase")
        return 2

    monkeypatch.setattr(store, "merge", merge, raising=False)
    monkeypatch.setattr(store, "rebase", rebase, raising=False)
    await workspace.ensure(store, "edit")
    assert await workspace.land(store, "edit") == "rebased"
    assert calls == ["merge", "rebase", "merge"]


async def test_a_conflicting_rebase_replays_the_ref_rather_than_asking_for_a_resolution(
    store, monkeypatch
):
    """Main is generated: re-running the pipeline rewrites every file, so a rebase conflicts on
    files nobody touched. The ref's own content is the intent, and it wins by construction."""
    merges = iter([workspace.StoreError("not a fast-forward", status=409), None])

    async def merge(ref, into=MAIN):
        if (answer := next(merges)) is not None:
            raise answer

    async def rebase(ref):
        raise workspace.StoreError("rebase conflicts", status=409)

    monkeypatch.setattr(store, "merge", merge, raising=False)
    monkeypatch.setattr(store, "rebase", rebase, raising=False)
    await workspace.ensure(store, "edit")

    assert await workspace.land(store, "edit") == "replayed"
    assert store.made[-1] == ("edit", MAIN), "recreated from main, so the merge is a fast-forward"
    assert drafts.changes("edit").dirty is False, "the draft was re-synced at the new revision"
