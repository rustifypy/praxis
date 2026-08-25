"""The editor's tools, driven directly — no graph, so `emit` takes its real no-op path.

Almost every test here asserts the same thing from a different angle: **the agent writes
nothing**. It proposes, the gate still runs on the proposal, and the store is untouched until a
person saves. That was not true before — a write was a commit and Cube was serving it seconds
later — so these exist to keep it from quietly becoming true again.
"""

import pytest
import yaml

from analyst_agent.agent.tools import editor as tools
from analyst_agent.agent.tools import trace
from analyst_agent.core import db
from analyst_agent.core import transcript as _transcript
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
    model = {"cubes/album.yml": dumped(ALBUM), "views/sales.yml": "views: []\n"}
    return model_store(main=model, edit=dict(model))


@pytest.fixture
async def ctx(tmp_path, store) -> tools.EditContext:
    await db.create_tables()
    return tools.EditContext(store=store, ref="edit")


def named(built: list, name: str):
    return next(t for t in built if t.name == name)


@pytest.fixture
def traced(monkeypatch):
    """What the tools put on the trace, patched on `trace` — which authors every `tool_start` and
    `tool_end` in the codebase."""
    captured: list[dict] = []
    monkeypatch.setattr(trace, "emit", lambda event: captured.append(event.model_dump()))
    return captured


@pytest.fixture
def proposals(monkeypatch):
    """The `proposal` events, which the tool module emits directly rather than through `trace` —
    they are not a trace entry, they are the body of the change being offered."""
    captured: list[dict] = []
    monkeypatch.setattr(tools, "emit", lambda event: captured.append(event.model_dump()))
    return captured


# --- reading ------------------------------------------------------------------


async def test_listing_reports_the_ref_and_the_files(ctx):
    out = await named(build := tools.build_editor_tools(ctx), "list_model_files").ainvoke({})
    assert "ref: edit" in out and "cubes/album.yml" in out
    assert "views/sales.yml" in out
    assert build  # the tool set is what the agent gets


async def test_reading_a_file_that_is_not_there_points_at_the_listing(ctx):
    out = await named(tools.build_editor_tools(ctx), "read_model_file").ainvoke(
        {"path": "cubes/nope.yml"}
    )
    assert "list_model_files" in out


async def test_reading_sees_what_is_staged_and_not_what_is_committed(store):
    """The buffer arrives with the message. An agent reading only the committed ref would propose
    a change that reverts whatever the user accepted a minute ago."""
    ctx = tools.EditContext(
        store=store,
        ref="edit",
        staged={"cubes/album.yml": dumped({**ALBUM, "description": "One album."})},
    )
    out = await named(tools.build_editor_tools(ctx), "read_model_file").ainvoke(
        {"path": "cubes/album.yml"}
    )
    assert "One album." in out


# --- proposing ----------------------------------------------------------------


async def test_proposing_on_main_is_refused(store):
    """Not a UI rule — main is what the pipeline generates, and a tool call bypasses the UI."""
    propose = named(tools.build_editor_tools(tools.EditContext(store, MAIN)), "propose_model_file")
    out = await propose.ainvoke({"path": "views/sales.yml", "text": "views: []\n"})
    assert "create a branch" in out
    assert store.commits == []


async def test_a_proposal_that_breaks_a_cube_is_refused_with_the_reason(ctx):
    """The gate runs on a proposal even though nothing is written: the reason is only useful
    while the agent still holds why it wrote that YAML. Told at Save instead, it is the person
    who has to explain it back."""
    propose = named(tools.build_editor_tools(ctx), "propose_model_file")
    renamed = {**ALBUM, "measures": [{"name": "total", "type": "count"}]}
    out = await propose.ainvoke({"path": "cubes/album.yml", "text": dumped(renamed)})
    assert "EDIT REJECTED" in out and "album.count was removed" in out
    assert ctx.staged == {}, "a refused proposal must not enter the overlay"


async def test_an_allowed_proposal_is_staged_and_nothing_is_committed(ctx, store):
    described = dumped({**ALBUM, "description": "One album."})
    propose = named(tools.build_editor_tools(ctx), "propose_model_file")
    out = await propose.ainvoke({"path": "cubes/album.yml", "text": described})

    assert "Proposed cubes/album.yml" in out and "nothing is saved" in out
    assert ctx.staged == {"cubes/album.yml": described}
    # The whole point of the change: the ref is exactly as it was.
    assert store.refs["edit"]["cubes/album.yml"] == dumped(ALBUM)
    assert store.commits == []


async def test_a_second_proposal_builds_on_the_first(ctx):
    """Two proposals in one turn: without the overlay the second would be written against the
    committed file and silently drop the first."""
    propose = named(tools.build_editor_tools(ctx), "propose_model_file")
    described = dumped({**ALBUM, "description": "One album."})
    await propose.ainvoke({"path": "cubes/album.yml", "text": described})

    read = named(tools.build_editor_tools(ctx), "read_model_file")
    assert "One album." in await read.ainvoke({"path": "cubes/album.yml"})


async def test_an_added_measure_carries_the_ambiguity_warning(ctx):
    body = {**ALBUM, "measures": [*ALBUM["measures"], {"name": "m", "type": "sum", "sql": "a * b"}]}
    out = await named(tools.build_editor_tools(ctx), "propose_model_file").ainvoke(
        {"path": "cubes/album.yml", "text": dumped(body)}
    )
    assert "Proposed" in out and "unqualified" in out


# --- proposing a delete -------------------------------------------------------


async def test_proposing_to_delete_a_cube_file_is_refused(ctx, store):
    out = await named(tools.build_editor_tools(ctx), "propose_delete").ainvoke(
        {"path": "cubes/album.yml"}
    )
    assert "EDIT REJECTED" in out
    assert "cubes/album.yml" in store.refs["edit"]
    assert ctx.staged == {}


async def test_proposing_to_delete_a_view_stages_the_removal(ctx, store):
    store.refs["edit"]["views/sales.yml"] = "views:\n- name: sales\n  cubes: []\n"
    out = await named(tools.build_editor_tools(ctx), "propose_delete").ainvoke(
        {"path": "views/sales.yml"}
    )
    assert "Proposed deleting views/sales.yml" in out
    # Staged as None, which is how one overlay carries both writes and removals.
    assert ctx.staged == {"views/sales.yml": None}
    assert "views/sales.yml" in store.refs["edit"], "nothing is deleted until the user saves"


async def test_a_staged_delete_disappears_from_the_listing(ctx, store):
    store.refs["edit"]["views/sales.yml"] = "views:\n- name: sales\n  cubes: []\n"
    ctx.staged["views/sales.yml"] = None
    out = await named(tools.build_editor_tools(ctx), "list_model_files").ainvoke({})
    assert "views/sales.yml" not in out


# --- what reaches the transcript ----------------------------------------------


async def test_the_body_travels_on_the_proposal_and_not_on_the_trace(ctx, traced, proposals):
    """`EVENT_KEYS` is the whole list of what an editor tool's trace may carry, and a file body is
    not on it: the trace records that something happened, and a second copy of the model in it
    would grow every event by the size of the file. The body has exactly one carrier."""
    described = dumped({**ALBUM, "description": "One album."})
    await named(tools.build_editor_tools(ctx), "propose_model_file").ainvoke(
        {"path": "cubes/album.yml", "text": described}
    )

    assert traced, "the tool put nothing on the trace"
    for event in traced:
        extra = set(event["data"]) - tools.EVENT_KEYS
        assert not extra, f"{event['data'].get('tool')} leaked {extra} into the transcript"

    assert [event["type"] for event in proposals] == ["proposal"]
    assert proposals[0]["data"] == {"path": "cubes/album.yml", "text": described, "ref": "edit"}


async def test_a_proposed_delete_says_so_with_a_null_body(ctx, store, proposals):
    """One event type for both, because the browser's question is the same — show me what would
    change — and a null body is what "this file goes" looks like as a diff."""
    store.refs["edit"]["views/sales.yml"] = "views:\n- name: sales\n  cubes: []\n"
    await named(tools.build_editor_tools(ctx), "propose_delete").ainvoke(
        {"path": "views/sales.yml"}
    )
    assert proposals[0]["data"] == {"path": "views/sales.yml", "text": None, "ref": "edit"}


async def test_a_refused_proposal_emits_no_proposal_event(ctx, proposals):
    """Otherwise the editor would open a diff for a change it can never save."""
    renamed = {**ALBUM, "measures": [{"name": "total", "type": "count"}]}
    await named(tools.build_editor_tools(ctx), "propose_model_file").ainvoke(
        {"path": "cubes/album.yml", "text": dumped(renamed)}
    )
    assert proposals == []


# --- the shape of the tool set ------------------------------------------------


async def test_the_editor_can_only_propose(ctx):
    """No write, no commit, no merge, no ref creation. Landing work is the user's, and after this
    change so is saving it."""
    names = {t.name for t in tools.build_editor_tools(ctx)}
    assert names == {
        "list_model_files",
        "read_model_file",
        "propose_model_file",
        "propose_delete",
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
