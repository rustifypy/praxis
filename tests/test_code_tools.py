"""The sandbox tools, driven directly.

`export_result` builds its CSV here and hands it over, so everything about it runs with no service
and no docker — which is the same property that lets an export work while the image is still
building. `run_python` needs a container, and what a script does once it runs is checked against a
live one by `make sandbox-check` rather than mocked here.
"""

import asyncio
import csv

import pytest

from analyst_agent import transcript as _transcript
from analyst_agent.agent.session import QueryRecord, Session
from analyst_agent.agent.tools import code as tools
from analyst_agent.agent.tools import trace
from analyst_agent.core import db
from analyst_agent.core.events import Event, emit
from analyst_agent.sandbox import Run

CHAT = "beefcafe0001"
COLUMNS = [
    {
        "id": "sales.month",
        "label": "Month",
        "kind": "dimension",
        "type": "time",
        "format": "",
        "align": "left",
    },
    {
        "id": "sales.invoice_total",
        "label": "Invoice total",
        "kind": "measure",
        "type": "number",
        "format": "",
        "align": "right",
    },
]
ROWS = [
    {"sales.month": "2026-01-01T00:00:00.000", "sales.invoice_total": 100},
    {"sales.month": "2026-02-01T00:00:00.000", "sales.invoice_total": 125},
]


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from analyst_agent.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
async def session(tmp_path, catalog, cube):
    """A Session with a stored result behind it, which is what `export_result` reads."""
    await db.create_tables()
    store = _transcript
    chat = await store.create("test", kind="question")
    result_id = await store.save_result(chat.id, "turn-1", COLUMNS, ROWS)

    built = Session(
        catalog=catalog,
        client=cube,
        grounding=None,
        chat_id=CHAT,
        turn_id="turn-1",
    )
    built.executed.append(
        QueryRecord(
            query={"measures": ["sales.invoice_total"]},
            sql="SELECT 1",
            row_count=len(ROWS),
            duration_ms=7,
            warnings=[],
            columns=COLUMNS,
            result_id=result_id,
        )
    )
    return built


@pytest.fixture
def events(monkeypatch):
    """What the tools say. Collected here; one test below leaves `emit` alone instead.

    Two patch points because these tools author two kinds of event: `trace` writes every
    tool_start and tool_end, and `code` writes `output` and `approval` itself — neither of
    which is a step's frame.
    """
    captured: list[dict] = []
    collect = lambda event: captured.append(event.model_dump())  # noqa: E731
    monkeypatch.setattr(trace, "emit", collect)
    monkeypatch.setattr(tools, "emit", collect)
    return captured


def named(built: list, name: str):
    return next(t for t in built if t.name == name)


class FakeSandbox:
    """The service replaced at the client seam — the same substitution as before, one layer out.
    What a real one does with a real signal is its own suite's job."""

    def __init__(self):
        self.ran: list[str] = []
        self.killed: list[str] = []
        self.uploaded: list[tuple[str, bytes]] = []
        self.on_run = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def aclose(self):
        return None

    async def run(self, chat_id, script, *, on_output=None):
        self.ran.append(script)
        if self.on_run is None:
            return Run(code=0, out="done\n", script="scripts/run-001.py")
        return await self.on_run(chat_id, script, on_output)

    async def kill(self, chat_id):
        self.killed.append(chat_id)
        return True

    async def upload(self, chat_id, filename, data):
        self.uploaded.append((filename, data))
        return {"path": f"uploads/{filename}"}


@pytest.fixture
def sandbox(monkeypatch):
    """A fake service, and a way to say what a run does. Returns the fake so a test can read
    what reached it."""
    fake = FakeSandbox()

    async def opened(*args, **kwargs):
        return fake

    monkeypatch.setattr(tools, "open_sandbox", opened)

    def run(on_run=None):
        fake.on_run = on_run
        return fake

    run.fake = fake  # type: ignore[attr-defined]
    return run


# --- the surface --------------------------------------------------------------


async def test_the_toolset_is_two_tools(session):
    built = tools.build_code_tools(session)
    assert {t.name for t in built} == {"export_result", "run_python"}


async def test_a_session_with_no_chat_still_names_a_workspace(catalog, cube, sandbox):
    """Only tests build one. It must not land on the workspace root itself, which is the whole
    volume rather than one chat's corner of it."""
    fake = sandbox()
    session = Session(catalog=catalog, client=cube, grounding=None)
    await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "x"})
    assert fake.ran, "a chatless session still runs"


# --- export_result ------------------------------------------------------------


async def test_exporting_hands_over_the_rows_with_member_ids_as_the_header(session, sandbox):
    """Member ids, not labels: a script refers to `sales.invoice_total`, which is what the agent
    knows the column by. The download route writes labels, because a person reads that file."""
    fake = sandbox()
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "2 rows" in out and "sales.invoice_total" in out

    name, data = fake.uploaded[0]
    assert name == "result.csv"
    rows = list(csv.reader(data.decode().splitlines()))
    assert rows[0] == ["sales.month", "sales.invoice_total"]
    assert [r[1] for r in rows[1:]] == ["100", "125"]


async def test_exporting_takes_a_filename(session, sandbox):
    fake = sandbox()
    await named(tools.build_code_tools(session), "export_result").ainvoke(
        {"filename": "monthly.csv"}
    )
    assert fake.uploaded[0][0] == "monthly.csv"


async def test_the_path_a_csv_lands_on_is_the_services_answer(session, sandbox):
    """The name is the agent's and the path is the sandbox's — one place decides it, on the side
    that owns the directory, so `..` and an absolute path are checked once."""
    sandbox()
    out = await named(tools.build_code_tools(session), "export_result").ainvoke(
        {"filename": "../../analyst.db"}
    )
    assert "uploads/" in out


async def test_an_export_with_no_sandbox_says_so_rather_than_claiming_success(session):
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "not available" in out and "no sandbox" in out


async def test_exporting_before_any_query_says_to_query_first(catalog, cube):
    session = Session(catalog=catalog, client=cube, grounding=None, chat_id=CHAT)
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "No query has run yet" in out and "run_query" in out


async def test_exporting_a_result_that_was_never_stored_says_to_re_run(catalog, cube, data_dir):
    """`run_query` stores rows only when the session has a store and a chat. Without one there is
    a QueryRecord and no rows, and the tool must say so rather than write an empty file."""
    session = Session(catalog=catalog, client=cube, grounding=None, chat_id=CHAT)
    session.executed.append(
        QueryRecord(query={}, sql=None, row_count=2, duration_ms=1, warnings=[], columns=COLUMNS)
    )
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "was not kept" in out
    assert not (data_dir / "workspaces" / CHAT / "result.csv").exists()


# --- the script that gets run -------------------------------------------------


async def test_the_script_the_run_landed_on_is_reported_back(session, sandbox):
    """Naming the file is what leaves a computed figure's provenance somewhere. The service saves
    it and numbers it — its own suite covers that — and this side has to say where."""
    sandbox()
    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "x"})
    assert "scripts/run-001.py" in out


# --- stopping a script --------------------------------------------------------


async def test_pressing_stop_asks_the_service_to_kill_the_run(session, sandbox):
    """The defect this closes: the turn loop only tests the flag between stream chunks, and a run
    is one `await` no chunk interrupts. So the tool watches the flag itself — and asks the service
    rather than cancelling the request, which would leave the script running unread."""
    session.cancel = asyncio.Event()

    async def running(chat_id, script, on_output):
        session.cancel.set()
        await asyncio.sleep(0.1)
        return Run(code=143, script="scripts/run-001.py")

    fake = sandbox(running)
    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "pass"})

    assert fake.killed == [CHAT]
    assert "stopped by the user" in out


async def test_a_script_that_finishes_first_is_never_killed(session, sandbox):
    session.cancel = asyncio.Event()
    fake = sandbox()
    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "pass"})

    assert fake.killed == []
    assert "stopped" not in out


async def test_nothing_runs_at_all_once_the_turn_has_been_stopped(session, sandbox):
    session.cancel = asyncio.Event()
    session.cancel.set()
    fake = sandbox()

    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "pass"})
    assert fake.ran == []
    assert "stopped this turn" in out


# --- what a running script says ----------------------------------------------


async def test_output_is_streamed_as_it_arrives_and_carries_nothing_but_a_delta(
    session, sandbox, events
):
    async def running(chat_id, script, on_output):
        on_output("half a line")
        await asyncio.sleep(tools.STREAM_INTERVAL_S)
        on_output(" and the rest\n")
        return Run(code=0, out="half a line and the rest\n", script="scripts/run-001.py")

    sandbox(running)
    await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "print(1)"})

    streamed = [e["data"] for e in events if e["type"] == "output"]
    assert "".join(d["delta"] for d in streamed) == "half a line and the rest\n"
    for data in streamed:
        assert not set(data) - tools.EVENT_KEYS


async def test_a_chatty_script_stops_being_streamed_rather_than_flooding_the_page(
    session, sandbox, events, monkeypatch
):
    monkeypatch.setattr(tools, "STREAM_LIMIT", 20)
    monkeypatch.setattr(tools, "STREAM_INTERVAL_S", 0)

    async def running(chat_id, script, on_output):
        for _ in range(50):
            on_output("x" * 10)
        return Run(code=0, out="x" * 500, script="scripts/run-001.py")

    sandbox(running)
    await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "print(1)"})

    streamed = "".join(e["data"]["delta"] for e in events if e["type"] == "output")
    assert 0 < len(streamed) <= 30, "the live view kept going past its limit"


# --- approving a script -------------------------------------------------------


async def ran(session, sandbox, script="print(1)"):
    """Drive `run_python` against a service that always succeeds, and say what reached it."""
    fake = sandbox()
    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": script})
    return fake.ran, out


async def test_a_script_waits_for_a_click_and_the_event_carries_it_whole(session, sandbox, events):
    """The one event that carries a script body. It is never stored — the workspace holds the
    file, and this is asking about that file."""
    session.approvals = asyncio.Queue()
    session.approvals.put_nowait("yes")

    seen, out = await ran(session, sandbox, "print('hello')")

    asked = [e["data"] for e in events if e["type"] == "approval"]
    assert len(asked) == 1
    assert asked[0]["script"] == "print('hello')"
    assert asked[0]["label"] == "run this script"
    assert not set(asked[0]) - tools.EVENT_KEYS
    assert seen, "an approved script did not run"
    assert "exited 0" in out


async def test_a_declined_script_never_reaches_the_service(session, sandbox, events):
    session.approvals = asyncio.Queue()
    session.approvals.put_nowait("no")

    seen, out = await ran(session, sandbox)

    assert seen == []
    assert "declined to run" in out
    assert [e["data"]["note"] for e in events if e["type"] == "tool_end"] == [
        "declined by the user"
    ]


async def test_always_stops_asking_for_the_rest_of_the_turn(session, sandbox, events):
    """Otherwise a write-run-fix loop is fifteen clicks, and the feature gets turned off."""
    session.approvals = asyncio.Queue()
    session.approvals.put_nowait("always")

    run = named(tools.build_code_tools(session), "run_python")
    sandbox()
    for _ in range(3):
        assert "exited 0" in await run.ainvoke({"script": "print(1)"})

    assert len([e for e in events if e["type"] == "approval"]) == 1


async def test_a_stop_while_the_question_is_open_answers_it_with_no(session, sandbox):
    """Nobody is coming to click: the tab closed, which sets the same flag the stop button does.
    Without this the turn waits on a queue nothing will ever be put on."""
    session.approvals = asyncio.Queue()
    session.cancel = asyncio.Event()

    async def leave():
        await asyncio.sleep(0.05)
        session.cancel.set()

    gone = asyncio.create_task(leave())
    seen, out = await ran(session, sandbox)
    await gone

    assert seen == []
    assert "declined to run" in out


async def test_with_no_queue_nothing_is_asked(session, sandbox, events):
    """`approve_scripts = false`, and the tool must not wait on a queue that is not there."""
    seen, out = await ran(session, sandbox)

    assert seen and "exited 0" in out
    assert [e for e in events if e["type"] == "approval"] == []


# --- the turn's budget --------------------------------------------------------


async def test_a_turn_that_has_spent_its_output_budget_refuses_the_next_script(
    session, sandbox, monkeypatch
):
    """Per-call caps already exist; this is the one that stops forty of them adding up."""
    monkeypatch.setattr(tools, "TURN_OUTPUT", 5)

    async def running(chat_id, script, on_output):
        return Run(code=0, out="hello world\n", script="scripts/run-001.py")

    sandbox(running)
    run = named(tools.build_code_tools(session), "run_python")

    assert "exited 0" in await run.ainvoke({"script": "print(1)"})
    refused = await run.ainvoke({"script": "print(2)"})
    assert "budget is spent" in refused
    assert "already computed" in refused


# --- what reaches the transcript ---------------------------------------------


async def test_no_event_carries_the_rows_or_the_script(session, events):
    """A transcript records that a script ran and how it ended. The workspace holds the script
    and the data — `EVENT_KEYS` is what stops a body being added to an event later."""
    await named(tools.build_code_tools(session), "export_result").ainvoke({})
    await named(tools.build_code_tools(session), "export_result").ainvoke({"filename": "../out"})

    assert events, "the tools emitted nothing at all"
    for event in events:
        extra = set(event["data"]) - tools.EVENT_KEYS
        assert not extra, f"{event['data'].get('tool')} added {extra} to an event"
        assert "125" not in str(event["data"]), "an event carried an exported row"


async def test_a_refusal_is_an_event_and_a_message_the_model_can_act_on(session, events):
    await named(tools.build_code_tools(session), "export_result").ainvoke({"filename": "/etc/x"})
    assert [e["data"]["ok"] for e in events if e["type"] == "tool_end"] == [False]
    assert events[-1]["data"]["errors"]


async def test_the_tools_run_with_the_real_emit_left_alone(session, sandbox):
    """Every other test here monkeypatches `emit`, which is exactly what once hid a TypeError
    raised while an event was being built. Outside a graph the real `emit` is a no-op, so this
    drives a tool through it."""
    assert emit(Event(type="tool_start", data={"tool": "x"})) is None
    fake = sandbox()
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "2 rows" in out
    assert fake.uploaded
