"""`run_query` and `set_chart` driven for real, with nothing about events replaced.

The other tool tests monkeypatch `emit`, which is what hid a `TypeError` raised while
*building* an event payload: the tool was serialising a pydantic `ChartSpec` with
`dataclasses.asdict`, every event assertion passed, and the only failure was in a live chat.
So these invoke the tools with `emit` left alone — outside a graph it is a no-op — and assert
the payload is what reaches the browser, which means asserting it is JSON.
"""

import json

import pytest

from analyst_agent.agent.session import Session
from analyst_agent.agent.tools import query as tools_module
from analyst_agent.agent.tools import trace
from analyst_agent.agent.tools.query import build_query_tools
from analyst_agent.core.events import Event, emit
from analyst_agent.datamodel.grounding import Grounding

QUERY = {
    "measures": ["orders.net_revenue"],
    "dimensions": ["orders.status"],
    "order": {"orders.net_revenue": "desc"},
    "limit": 3,
}
ROWS = [{"orders.status": "paid", "orders.net_revenue": 100}]


@pytest.fixture
def tools(cube, catalog):
    session = Session(catalog=catalog, client=cube, grounding=Grounding({}))
    return {t.name: t for t in build_query_tools(session)}, session


async def test_emit_outside_a_graph_is_a_no_op_rather_than_an_error():
    """The path every one of these tools takes when a test drives it directly."""
    assert emit(Event(type="tool_start", data={"tool": "x"})) is None


async def test_run_query_builds_an_event_payload_that_survives_the_wire(tools, cube):
    """A chart is a pydantic model and a column is a dataclass; serialising either with the
    other's function raises while the event is being built, and takes the turn with it."""
    cube.rows = ROWS
    (registry, session) = tools

    answer = await registry["run_query"].ainvoke({"query": QUERY})

    assert "1 rows" in answer
    record = session.executed[-1]
    # What `tool_end` carries. `json.dumps` is the assertion: the event goes over SSE.
    payload = {"columns": record.columns, "chart": None, "preview": ROWS}
    assert json.loads(json.dumps(payload, default=None))["columns"][0]["id"] == "orders.status"


async def test_set_chart_serialises_the_spec_it_accepted(tools, cube):
    cube.rows = ROWS
    (registry, _) = tools
    await registry["run_query"].ainvoke({"query": QUERY})

    answer = await registry["set_chart"].ainvoke(
        {"spec": {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}}
    )
    assert "bar chart" in answer


async def test_a_rejected_query_comes_back_as_text_to_act_on(tools):
    (registry, session) = tools
    answer = await registry["run_query"].ainvoke({"query": {"measures": ["orders.nope"]}})

    assert "QUERY REJECTED" in answer and "run_query again" in answer
    assert session.rejected and not session.executed


async def test_the_playground_order_shape_is_refused_by_name(tools):
    """`[{"id": ..., "desc": true}]` is the Playground's format. The rejection has to say so,
    or the agent retries the same shape."""
    (registry, _) = tools
    answer = await registry["run_query"].ainvoke(
        {
            "query": {
                "measures": ["orders.net_revenue"],
                "order": [{"id": "orders.net_revenue", "desc": True}],
            }
        }
    )
    assert "Playground" in answer
    assert '{"member": "asc"|"desc"}' in answer


async def test_no_query_tool_invents_an_event_key(tools, cube, monkeypatch):
    """`EVENT_KEYS` is the whole list of what a query event may carry. These events *do* hold
    state — a replayed transcript draws the table from `tool_end` alone — so the guard is not
    that they are stateless but that the browser knows every key it may be handed."""
    captured: list[dict] = []
    monkeypatch.setattr(trace, "emit", lambda event: captured.append(event.model_dump()))
    cube.rows = ROWS
    (registry, _) = tools

    await registry["search_model"].ainvoke({"question": "revenue"})
    await registry["run_query"].ainvoke({"query": QUERY})
    await registry["run_query"].ainvoke({"query": {"measures": ["orders.nope"]}})
    await registry["set_chart"].ainvoke(
        {"spec": {"type": "bar", "x": "orders.status", "y": ["orders.net_revenue"]}}
    )
    await registry["explain_last_query"].ainvoke({})
    await registry["load_grounding"].ainvoke({"name": "nothing"})

    assert captured, "the tools emitted nothing at all"
    for event in captured:
        extra = set(event["data"]) - tools_module.EVENT_KEYS
        assert not extra, f"{event['data'].get('tool')} leaked {extra} into the transcript"
