"""One turn's event stream, without a model.

`run_turn` is given a callable that builds the agent, so a build that fails is how the turn's own
bookkeeping — the user event, the stored transcript, the error and the `done` — is checked with
nothing spent.
"""

import pytest

from analyst_agent.agent.turn import run_turn
from analyst_agent.transcript import ChatStore


@pytest.fixture
async def store(tmp_path):
    s = ChatStore(tmp_path / "analyst.db")
    await s.init()
    return s


async def no_agent(_turn_id: str):
    raise RuntimeError("no model here")


async def test_an_attached_file_is_recorded_on_the_user_event(store):
    """An upload happens between turns and has no turn of its own, so what records it is the user
    event of the turn it arrived with. Without that, a replayed chat shows a script reading a file
    that came from nowhere."""
    chat = await store.create()
    events = [
        e
        async for e in run_turn(
            no_agent, chat.id, "what is this?", store, None, ["uploads/orders.csv"]
        )
    ]

    assert events[0].type == "user"
    assert events[0].data == {"text": "what is this?", "files": ["uploads/orders.csv"]}
    # And it is in the transcript, not only on the wire.
    stored = await store.events(chat.id)
    assert stored[0]["data"]["files"] == ["uploads/orders.csv"]


async def test_a_turn_with_no_files_says_so_rather_than_omitting_the_key(store):
    chat = await store.create()
    events = [e async for e in run_turn(no_agent, chat.id, "hello", store, None)]
    assert events[0].data == {"text": "hello", "files": []}


async def test_a_failed_build_is_a_transcript_entry_rather_than_an_exception(store):
    chat = await store.create()
    events = [e async for e in run_turn(no_agent, chat.id, "hello", store, None)]
    assert [e.type for e in events] == ["user", "error", "done"]
    assert "no model here" in events[1].data["message"]
