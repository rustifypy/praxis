"""The editor's tools, driven directly — no graph, so `emit` takes its real no-op path."""

import pytest
import yaml

from analyst_agent.chat.store import ChatStore
from analyst_agent.dashboards.store import DashboardStore
from analyst_agent.sources import registry
from analyst_agent.sources.edit import tools
from analyst_agent.sources.model import vcs
from analyst_agent.sources.registry import Source

SOURCE = Source(name="shop", description="", driver="postgres", port=4001)
ALBUM = {
    "name": "album",
    "sql_table": "public.album",
    "measures": [{"name": "count", "type": "count"}],
    "dimensions": [{"name": "title", "sql": "title", "type": "string"}],
}


@pytest.fixture
async def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "project_root", lambda: tmp_path)
    base = registry.scaffold_source_dirs(SOURCE, root=tmp_path)
    (base / "cubes" / "album.yml").write_text(yaml.safe_dump({"cubes": [ALBUM]}, sort_keys=False))
    await vcs.ensure(base)
    dashboards = DashboardStore(tmp_path / "analyst.db")
    await dashboards.init()
    return tools.EditContext(source=SOURCE, client=None, dashboards=dashboards)


def named(built: list, name: str):
    return next(t for t in built if t.name == name)


async def test_listing_reports_the_branch_and_the_files(ctx):
    out = await named(build := tools.build_editor_tools(ctx), "list_model_files").ainvoke({})
    assert "branch: main" in out and "cubes/album.yml" in out
    assert build  # the tool set is what the agent gets


async def test_writing_on_main_is_refused_before_anything_reaches_disk(ctx):
    """Not a UI rule — main is what the pipeline generates, and a tool call bypasses the UI."""
    write = named(tools.build_editor_tools(ctx), "write_model_file")
    out = await write.ainvoke({"path": "views/sales.yml", "text": "views: []\n"})
    assert "create a branch" in out
    assert not (ctx.repo / "views" / "sales.yml").exists()


async def test_a_write_that_breaks_a_cube_is_refused_with_the_reason(ctx):
    await vcs.create(ctx.repo, "edit")
    write = named(tools.build_editor_tools(ctx), "write_model_file")
    renamed = {**ALBUM, "measures": [{"name": "total", "type": "count"}]}
    out = await write.ainvoke(
        {"path": "cubes/album.yml", "text": yaml.safe_dump({"cubes": [renamed]}, sort_keys=False)}
    )
    assert "EDIT REJECTED" in out and "album.count was removed" in out
    # The file on disk is untouched, which is the point of gating before the write.
    assert "count" in (ctx.repo / "cubes" / "album.yml").read_text()


async def test_an_allowed_write_lands_and_says_it_is_already_live(ctx):
    """Cube watches the model mount in dev mode, so a save is serving before validate runs.
    The tool must not imply the container is holding it back."""
    await vcs.create(ctx.repo, "edit")
    write = named(tools.build_editor_tools(ctx), "write_model_file")
    described = {**ALBUM, "description": "One album."}
    out = await write.ainvoke(
        {"path": "cubes/album.yml", "text": yaml.safe_dump({"cubes": [described]}, sort_keys=False)}
    )
    assert "Wrote cubes/album.yml" in out and "live" in out
    assert "One album." in (ctx.repo / "cubes" / "album.yml").read_text()


async def test_an_added_measure_carries_the_ambiguity_warning(ctx):
    await vcs.create(ctx.repo, "edit")
    write = named(tools.build_editor_tools(ctx), "write_model_file")
    body = {**ALBUM, "measures": [*ALBUM["measures"], {"name": "m", "type": "sum", "sql": "a * b"}]}
    out = await write.ainvoke(
        {"path": "cubes/album.yml", "text": yaml.safe_dump({"cubes": [body]}, sort_keys=False)}
    )
    assert "Wrote" in out and "unqualified" in out


async def test_deleting_a_cube_file_is_refused(ctx):
    await vcs.create(ctx.repo, "edit")
    out = await named(tools.build_editor_tools(ctx), "delete_model_file").ainvoke(
        {"path": "cubes/album.yml"}
    )
    assert "EDIT REJECTED" in out
    assert (ctx.repo / "cubes" / "album.yml").is_file()


async def test_the_editor_cannot_merge_or_switch_branch(ctx):
    """Those restart the container and land work, so they stay the user's buttons."""
    names = {t.name for t in tools.build_editor_tools(ctx)}
    assert names == {
        "list_model_files",
        "read_model_file",
        "write_model_file",
        "delete_model_file",
        "validate_model",
        "commit_changes",
    }


async def test_commit_hands_the_merge_back_to_the_user(ctx):
    await vcs.create(ctx.repo, "edit")
    (ctx.repo / "cubes" / "album.yml").write_text(
        yaml.safe_dump({"cubes": [{**ALBUM, "description": "x"}]}, sort_keys=False)
    )
    out = await named(tools.build_editor_tools(ctx), "commit_changes").ainvoke(
        {"message": "describe album"}
    )
    assert "Committed" in out and "user's" in out
    assert await vcs.status(ctx.repo) == []


async def test_editor_chats_are_not_in_the_chat_list(tmp_path):
    """They belong to a source, not to the chat sidebar."""
    store = ChatStore(tmp_path / "analyst.db")
    await store.init()
    await store.create("What was revenue?")
    edit = await store.create_model_chat("shop", "add-margin")

    assert [c.title for c in await store.list_chats()] == ["What was revenue?"]
    assert [c["id"] for c in await store.model_chats("shop")] == [edit.id]
    assert await store.model_chat_source(edit.id) == "shop"
    assert await store.model_chat_source("nope") == ""
