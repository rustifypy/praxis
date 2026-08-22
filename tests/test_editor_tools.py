"""The editor's tools, driven directly — no graph, so `emit` takes its real no-op path."""

import pytest
import yaml

from analyst_agent import transcript as _transcript
from analyst_agent.agent.tools import editor as tools
from analyst_agent.core import db
from analyst_agent.core.deployment import MAIN

ALBUM = {
    "name": "album",
    "sql_table": "public.album",
    "measures": [{"name": "count", "type": "count"}],
    "dimensions": [{"name": "title", "sql": "title", "type": "string"}],
}


def dumped(cube: dict) -> str:
    return yaml.safe_dump({"cubes": [cube]}, sort_keys=False)


@pytest.fixture
def store(model_store):
    model = {"cubes/album.yml": dumped(ALBUM)}
    return model_store(main=model, edit=dict(model))


@pytest.fixture
async def ctx(tmp_path, store) -> tools.EditContext:
    await db.create_tables()
    return tools.EditContext(client=None, store=store, ref="edit")


def named(built: list, name: str):
    return next(t for t in built if t.name == name)


async def test_listing_reports_the_ref_and_the_files(ctx):
    out = await named(build := tools.build_editor_tools(ctx), "list_model_files").ainvoke({})
    assert "ref: edit" in out and "cubes/album.yml" in out
    assert build  # the tool set is what the agent gets


async def test_reading_a_file_that_is_not_there_points_at_the_listing(ctx):
    out = await named(tools.build_editor_tools(ctx), "read_model_file").ainvoke(
        {"path": "cubes/nope.yml"}
    )
    assert "list_model_files" in out


async def test_writing_on_main_is_refused_before_anything_is_committed(ctx, store):
    """Not a UI rule — main is what the pipeline generates, and a tool call bypasses the UI."""
    write = named(
        tools.build_editor_tools(tools.EditContext(None, store, MAIN)), "write_model_file"
    )
    out = await write.ainvoke({"path": "views/sales.yml", "text": "views: []\n"})
    assert "create a ref" in out
    assert store.commits == []


async def test_a_write_that_breaks_a_cube_is_refused_with_the_reason(ctx, store):
    write = named(tools.build_editor_tools(ctx), "write_model_file")
    renamed = {**ALBUM, "measures": [{"name": "total", "type": "count"}]}
    out = await write.ainvoke({"path": "cubes/album.yml", "text": dumped(renamed)})
    assert "EDIT REJECTED" in out and "album.count was removed" in out
    # Nothing was committed, which is the point of gating before the write.
    assert "count" in store.refs["edit"]["cubes/album.yml"]
    assert store.commits == []


async def test_an_allowed_write_lands_and_says_it_is_already_live(ctx, store):
    """Cube asks the store for the ref's version on every request, so a save is serving before
    validate runs. The tool must not imply something is holding it back."""
    described = {**ALBUM, "description": "One album."}
    write = named(tools.build_editor_tools(ctx), "write_model_file")
    out = await write.ainvoke({"path": "cubes/album.yml", "text": dumped(described)})
    assert "Wrote cubes/album.yml" in out and "live" in out
    assert "One album." in store.refs["edit"]["cubes/album.yml"]


async def test_an_added_measure_carries_the_ambiguity_warning(ctx):
    body = {**ALBUM, "measures": [*ALBUM["measures"], {"name": "m", "type": "sum", "sql": "a * b"}]}
    out = await named(tools.build_editor_tools(ctx), "write_model_file").ainvoke(
        {"path": "cubes/album.yml", "text": dumped(body)}
    )
    assert "Wrote" in out and "unqualified" in out


async def test_deleting_a_cube_file_is_refused(ctx, store):
    out = await named(tools.build_editor_tools(ctx), "delete_model_file").ainvoke(
        {"path": "cubes/album.yml"}
    )
    assert "EDIT REJECTED" in out
    assert "cubes/album.yml" in store.refs["edit"]


async def test_the_editor_cannot_merge_create_a_ref_or_commit(ctx):
    """Merging and creating a ref land work, so they stay the user's buttons — and there is no
    commit tool because every write is already a commit."""
    names = {t.name for t in tools.build_editor_tools(ctx)}
    assert names == {
        "list_model_files",
        "read_model_file",
        "write_model_file",
        "delete_model_file",
        "validate_model",
    }


async def test_editor_chats_are_not_in_the_chat_list(tmp_path):
    """They belong to the model editor, not to the chat sidebar."""
    await db.create_tables()
    store = _transcript
    await store.create("What was revenue?")
    edit = await store.create("Editing add-margin", "model", "add-margin")

    assert [c.title for c in await store.list_chats()] == ["What was revenue?"]
    assert [c.id for c in await store.list_chats("model")] == [edit.id]
    assert (await store.get(edit.id)).ref == "add-margin"
    assert await store.get("nope") is None
