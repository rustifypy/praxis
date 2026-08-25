"""One turn's event stream, without a model.

`run_turn` is given a callable that builds the agent, so a build that fails is how the turn's own
bookkeeping — the user event, the stored transcript, the error and the `done` — is checked with
nothing spent.
"""

import asyncio

import pytest

from analyst_agent.agent.turn import run_turn
from analyst_agent.core import db
from analyst_agent.core import transcript as _transcript


@pytest.fixture
async def store(tmp_path):
    await db.create_tables()
    s = _transcript
    return s


async def no_agent(_turn_id: str):
    raise RuntimeError("no model here")


def scripted(*items):
    """An agent whose stream is fixed. `run_turn` only ever calls `.astream`, so that is the whole
    of one; a callable among the items runs at that point instead of being yielded."""

    class Agent:
        async def astream(self, _input, config=None, stream_mode=None):
            for item in items:
                if callable(item):
                    item()
                else:
                    yield item

    async def build(_turn_id: str):
        return Agent()

    return build


async def test_an_attached_file_is_recorded_on_the_user_event(store):
    """An upload happens between turns and has no turn of its own, so what records it is the user
    event of the turn it arrived with. Without that, a replayed chat shows a script reading a file
    that came from nowhere."""
    chat = await store.create()
    events = [
        e async for e in run_turn(no_agent, chat.id, "what is this?", None, ["uploads/orders.csv"])
    ]

    assert events[0].type == "user"
    assert events[0].data == {"text": "what is this?", "files": ["uploads/orders.csv"]}
    # And it is in the transcript, not only on the wire.
    stored = await store.events(chat.id)
    assert stored[0]["data"]["files"] == ["uploads/orders.csv"]


async def test_a_turn_with_no_files_says_so_rather_than_omitting_the_key(store):
    chat = await store.create()
    events = [e async for e in run_turn(no_agent, chat.id, "hello", None)]
    assert events[0].data == {"text": "hello", "files": []}


async def test_a_failed_build_is_a_transcript_entry_rather_than_an_exception(store):
    chat = await store.create()
    events = [e async for e in run_turn(no_agent, chat.id, "hello", None)]
    assert [e.type for e in events] == ["user", "error", "done"]
    assert "no model here" in events[1].data["message"]


async def test_a_streamed_output_event_reaches_the_browser_but_not_the_transcript(store):
    """The whole of a script's output arrives on `tool_end`, trimmed. Storing the deltas too
    would put an untrimmed second copy in the event log."""
    chat = await store.create()
    build = scripted(
        ("custom", {"type": "tool_start", "data": {"tool": "run_python"}}),
        ("custom", {"type": "output", "data": {"tool": "run_python", "delta": "working\n"}}),
        ("custom", {"type": "tool_end", "data": {"tool": "run_python", "ok": True}}),
    )

    events = [e async for e in run_turn(build, chat.id, "go")]
    assert [e.type for e in events] == ["user", "tool_start", "output", "tool_end", "done"]

    stored = [e["type"] for e in await store.events(chat.id)]
    assert stored == ["user", "tool_start", "tool_end"]


async def test_a_tool_end_that_lands_as_the_turn_is_stopped_is_still_recorded(store):
    """A stopped script emits its own `tool_end`. Testing the flag before the event dropped it,
    and the turn replayed as a step that never finished."""
    cancel = asyncio.Event()
    chat = await store.create()
    build = scripted(
        ("custom", {"type": "tool_start", "data": {"tool": "run_python"}}),
        cancel.set,
        ("custom", {"type": "tool_end", "data": {"tool": "run_python", "ok": False}}),
        ("custom", {"type": "tool_start", "data": {"tool": "run_query"}}),
    )

    events = [e async for e in run_turn(build, chat.id, "go", cancel)]
    assert [e.type for e in events] == ["user", "tool_start", "tool_end", "done"]

    stored = await store.events(chat.id)
    assert [e["type"] for e in stored] == ["user", "tool_start", "tool_end"]
