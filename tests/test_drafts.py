import pytest

from analyst_agent.modelstore.editing import drafts

MODEL = {
    "cubes/orders.yml": "cubes:\n  - name: orders\n",
    "views/sales.yml": "views:\n  - name: sales\n",
}


@pytest.fixture
def draft():
    drafts.sync("edit-sales", MODEL, rev=4, sha="abc123")
    return "edit-sales"


def test_a_synced_draft_is_the_stores_content():
    assert drafts.exists("edit-sales") is False
    drafts.sync("edit-sales", MODEL, rev=4, sha="abc123")
    assert drafts.files("edit-sales") == MODEL
    assert drafts.changes("edit-sales").dirty is False
    origin = drafts.origin_at("edit-sales")
    assert (origin.ref, origin.rev, origin.sha) == ("edit-sales", 4, "abc123")


def test_no_draft_answers_empty_rather_than_raising():
    assert drafts.origin_at("nothing-here") is None
    assert drafts.files("nothing-here") == {}
    assert drafts.read("nothing-here", "cubes/orders.yml") is None


def test_an_edited_file_is_a_write_and_a_deleted_one_is_a_delete(draft):
    drafts.save(draft, "cubes/orders.yml", "cubes:\n  - name: orders\n    description: x\n")
    assert drafts.remove(draft, "views/sales.yml") is True

    changed = drafts.changes(draft)
    assert set(changed.write) == {"cubes/orders.yml"}
    assert changed.delete == ["views/sales.yml"]
    assert changed.dirty is True


def test_a_new_file_is_a_write_and_removing_it_leaves_nothing_to_publish(draft):
    drafts.save(draft, "views/new.yml", "views:\n  - name: new\n")
    assert set(drafts.changes(draft).write) == {"views/new.yml"}

    assert drafts.remove(draft, "views/new.yml") is True
    assert drafts.changes(draft).dirty is False


def test_rewriting_a_file_with_its_own_text_is_not_a_change(draft):
    drafts.save(draft, "cubes/orders.yml", MODEL["cubes/orders.yml"])
    assert drafts.changes(draft).dirty is False


def test_discarding_one_file_leaves_the_rest_of_the_draft(draft):
    drafts.save(draft, "cubes/orders.yml", "broken")
    drafts.save(draft, "views/sales.yml", "also edited")

    drafts.discard(draft, "cubes/orders.yml")
    assert drafts.read(draft, "cubes/orders.yml") == MODEL["cubes/orders.yml"]
    assert set(drafts.changes(draft).write) == {"views/sales.yml"}


def test_discarding_a_file_the_store_never_had_deletes_it(draft):
    drafts.save(draft, "views/invented.yml", "views: []\n")
    drafts.discard(draft, "views/invented.yml")
    assert drafts.read(draft, "views/invented.yml") is None


def test_discarding_the_whole_draft_restores_a_delete(draft):
    drafts.remove(draft, "views/sales.yml")
    drafts.save(draft, "cubes/orders.yml", "broken")

    drafts.discard(draft)
    assert drafts.files(draft) == MODEL
    assert drafts.changes(draft).dirty is False


def test_syncing_again_discards_local_edits(draft):
    drafts.save(draft, "cubes/orders.yml", "broken")
    drafts.sync(draft, MODEL, rev=5)
    assert drafts.files(draft) == MODEL
    assert drafts.origin_at(draft).rev == 5


def test_a_dropped_draft_is_gone(draft):
    drafts.drop(draft)
    assert drafts.exists(draft) is False
    assert drafts.files(draft) == {}


@pytest.mark.parametrize("path", ["../escape.yml", "/etc/passwd.yml", "", "cubes/../../x.yml"])
def test_a_path_that_leaves_the_model_is_refused(draft, path):
    with pytest.raises(ValueError):
        drafts.save(draft, path, "cubes: []\n")


@pytest.mark.parametrize("ref", ["edit/sales", "..", ".hidden", ""])
def test_a_ref_that_cannot_name_a_directory_is_refused(ref):
    with pytest.raises(ValueError):
        drafts.files(ref)


def test_a_dotted_file_in_the_draft_is_not_part_of_the_model(draft):
    (drafts._dir(draft) / "model" / "cubes" / ".orders.yml.swp").write_text("junk")
    assert drafts.files(draft) == MODEL
    assert drafts.changes(draft).dirty is False
