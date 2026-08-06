import pytest

from analyst_agent.chat.events import Event
from analyst_agent.chat.store import ChatStore


@pytest.fixture
async def store(tmp_path):
    s = ChatStore(tmp_path / "analyst.db")
    await s.init()
    return s


async def test_chats_are_listed_most_recently_touched_first(store):
    first = await store.create("first")
    second = await store.create("second")
    await store.append(first.id, "t1", Event(type="user", data={"text": "hi"}))

    assert [c.id for c in await store.list_chats()] == [first.id, second.id]


async def test_rename_and_delete(store):
    chat = await store.create()
    await store.rename(chat.id, "Q3 revenue")
    assert (await store.get(chat.id)).title == "Q3 revenue"

    await store.delete(chat.id)
    assert await store.get(chat.id) is None
    assert await store.events(chat.id) == []


async def test_events_replay_in_the_order_they_were_appended(store):
    chat = await store.create()
    for kind in ("user", "thinking", "tool_start", "tool_end", "text"):
        await store.append(chat.id, "t1", Event(type=kind, data={"k": kind}))

    replayed = await store.events(chat.id)
    assert [e["type"] for e in replayed] == [
        "user",
        "thinking",
        "tool_start",
        "tool_end",
        "text",
    ]
    assert [e["seq"] for e in replayed] == [1, 2, 3, 4, 5]


async def test_reserved_orchestrator_fields_survive_the_round_trip(store):
    chat = await store.create()
    await store.append(chat.id, "t1", Event(type="text", parent_id="abc", depth=2))
    event = (await store.events(chat.id))[0]
    assert (event["parent_id"], event["depth"]) == ("abc", 2)


async def test_deleting_a_chat_takes_its_events(store):
    keep = await store.create()
    drop = await store.create()
    await store.append(keep.id, "t1", Event(type="user"))
    await store.append(drop.id, "t1", Event(type="user"))

    await store.delete(drop.id)
    assert len(await store.events(keep.id)) == 1


async def test_replay_session_rebuilds_provenance_across_turns(store):
    """What makes `explain_last_query` work on turn three, and after a restart."""
    chat = await store.create()
    query = {"measures": ["x.count"]}
    await store.append(
        chat.id,
        "t1",
        Event(
            type="tool_end",
            data={
                "tool": "run_query",
                "ok": True,
                "query": query,
                "sql": "SELECT 1",
                "rows_total": 7,
                "duration_ms": 42,
                "warnings": ["w"],
            },
        ),
    )
    await store.append(
        chat.id,
        "t2",
        Event(
            type="tool_end",
            data={
                "tool": "run_query",
                "ok": False,
                "query": {"measures": ["nope"]},
                "errors": ["no such member"],
            },
        ),
    )

    executed, rejected = await store.replay_session(chat.id)
    assert len(executed) == 1
    assert executed[0].sql == "SELECT 1"
    assert executed[0].row_count == 7
    assert executed[0].query == query
    assert rejected == [{"query": {"measures": ["nope"]}, "errors": ["no such member"]}]


async def test_non_query_tool_events_are_not_provenance(store):
    chat = await store.create()
    await store.append(
        chat.id, "t1", Event(type="tool_end", data={"tool": "search_model", "ok": True})
    )
    assert await store.replay_session(chat.id) == ([], [])
