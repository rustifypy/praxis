"""One turn's event stream, without a model.

`run_turn` is given a callable that builds the agent, so a build that fails is how the turn's own
bookkeeping — the user event, the stored transcript, the error and the `done` — is checked with
nothing spent.
"""

import asyncio

import pytest

from praxis.agent.turn import run_turn, sink
from praxis.core import db
from praxis.core import transcript as _transcript


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
        e
        async for e in run_turn(
            no_agent, sink(chat.id), chat.id, "what is this?", None, ["uploads/orders.csv"]
        )
    ]

    assert events[0].type == "user"
    assert events[0].data == {"text": "what is this?", "files": ["uploads/orders.csv"]}
    # And it is in the transcript, not only on the wire.
    stored = await store.events(chat.id)
    assert stored[0]["data"]["files"] == ["uploads/orders.csv"]


async def test_a_turn_with_no_files_says_so_rather_than_omitting_the_key(store):
    chat = await store.create()
    events = [e async for e in run_turn(no_agent, sink(chat.id), chat.id, "hello", None)]
    assert events[0].data == {"text": "hello", "files": []}


async def test_a_failed_build_is_a_transcript_entry_rather_than_an_exception(store):
    chat = await store.create()
    events = [e async for e in run_turn(no_agent, sink(chat.id), chat.id, "hello", None)]
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

    events = [e async for e in run_turn(build, sink(chat.id), chat.id, "go")]
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

    events = [e async for e in run_turn(build, sink(chat.id), chat.id, "go", cancel)]
    assert [e.type for e in events] == ["user", "tool_start", "tool_end", "done"]

    stored = await store.events(chat.id)
    assert [e["type"] for e in stored] == ["user", "tool_start", "tool_end"]


async def test_the_sink_is_the_only_thing_that_decides_where_events_are_kept(store):
    """What lets a Run keep its events in `runs/<id>/events.jsonl` while a chat keeps them in
    sqlite, with one copy of the loop above. A sink that is not the transcript must leave the
    transcript empty — otherwise a Run would be recorded twice, in two formats, and the copies
    would drift."""
    chat = await store.create()
    kept: list[tuple[str, str]] = []

    async def elsewhere(turn_id: str, event) -> None:
        kept.append((turn_id, event.type))

    build = scripted(("custom", {"type": "tool_start", "data": {"tool": "run_query"}}))
    events = [e async for e in run_turn(build, elsewhere, "phase:01_pull", "go")]

    assert [e.type for e in events] == ["user", "tool_start", "done"]
    assert [kind for _, kind in kept] == ["user", "tool_start"]
    assert len({turn for turn, _ in kept}) == 1, "one turn id across the turn's events"
    assert await store.events(chat.id) == []


def answered(provider: str | None, threads: list[str]):
    """An agent with a checkpointed history last answered by `provider`, recording which thread
    each stream asked for."""
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage

    class Agent:
        checkpointer = object()

        async def aget_state(self, config):
            tag = {"model_provider": provider} if provider else {}
            answer = AIMessage("before", response_metadata=tag)
            return SimpleNamespace(values={"messages": [answer]})

        async def astream(self, _input, config=None, stream_mode=None):
            threads.append(config["configurable"]["thread_id"])
            return
            yield

    async def build(_turn_id: str):
        return Agent()

    return build


@pytest.fixture
def on_anthropic(monkeypatch):
    from praxis.core.config import settings

    monkeypatch.setattr(settings, "query_model", "anthropic:claude-opus-5-5")
    monkeypatch.setattr(settings, "on_provider_change", "refuse")
    return settings


async def test_a_chat_answered_by_another_provider_is_refused(store, on_anthropic):
    """Its history holds the other provider's own blocks, which this one would refuse mid-turn."""
    chat = await store.create()
    threads: list[str] = []
    events = [e async for e in run_turn(answered("openai", threads), sink(chat.id), chat.id, "hi")]

    assert threads == []
    assert [e.type for e in events] == ["user", "error", "done"]
    assert "answered by openai, and the model is now anthropic" in events[1].data["message"]


async def test_a_new_thread_is_one_per_provider(store, on_anthropic, monkeypatch):
    """Named by provider, so a switch back finds the original thread rather than a third one."""
    monkeypatch.setattr(on_anthropic, "on_provider_change", "new_thread")
    chat = await store.create()
    threads: list[str] = []
    events = [e async for e in run_turn(answered("openai", threads), sink(chat.id), chat.id, "hi")]

    assert threads == [f"{chat.id}@anthropic"]
    assert [e.type for e in events] == ["user", "done"]


@pytest.mark.parametrize("provider", ["anthropic", None])
async def test_a_chat_continues_on_its_own_provider_or_an_unknown_one(
    store, on_anthropic, provider
):
    chat = await store.create()
    threads: list[str] = []
    [e async for e in run_turn(answered(provider, threads), sink(chat.id), chat.id, "hi")]

    assert threads == [chat.id]
