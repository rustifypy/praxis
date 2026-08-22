import pytest

from analyst_agent import transcript as _transcript
from analyst_agent.agent.session import replay
from analyst_agent.core import db
from analyst_agent.core.events import Event


@pytest.fixture
async def store(tmp_path):
    await db.create_tables()
    s = _transcript
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

    await store.delete_chat(chat.id)
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

    await store.delete_chat(drop.id)
    assert len(await store.events(keep.id)) == 1


async def test_replay_rebuilds_provenance_across_turns(store):
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

    executed, rejected = replay(await store.events(chat.id))
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
    assert replay(await store.events(chat.id)) == ([], [])


COLUMNS = [
    {"id": "orders.status", "label": "Status", "kind": "dimension", "align": "left"},
    {"id": "orders.count", "label": "Count", "kind": "measure", "align": "right"},
]


async def test_a_saved_result_round_trips_whole(store):
    """The point of the table: the rows the event log deliberately does not carry."""
    chat = await store.create()
    rows = [{"orders.status": f"s{i}", "orders.count": i} for i in range(250)]
    result_id = await store.save_result(chat.id, "t1", COLUMNS, rows)

    record = await store.result(result_id)
    assert record["row_count"] == 250
    assert record["rows"] == rows
    assert record["columns"] == COLUMNS
    assert (record["chat_id"], record["turn_id"]) == (chat.id, "t1")


async def test_an_unknown_result_id_is_none_not_an_error(store):
    assert await store.result("nope") is None


async def test_deleting_a_chat_takes_its_results(store):
    keep = await store.create()
    drop = await store.create()
    kept = await store.save_result(keep.id, "t1", COLUMNS, [{"orders.count": 1}])
    dropped = await store.save_result(drop.id, "t1", COLUMNS, [{"orders.count": 2}])

    await store.delete_chat(drop.id)
    assert await store.result(dropped) is None
    assert await store.result(kept) is not None
