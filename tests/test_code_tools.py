"""The sandbox tools, driven directly.

`export_result` is host-side against the bind mount, so everything about it runs with no container
and no docker — which is the same property that lets an export work while the image is still
building. `run_python` needs a container, and what a script does once it runs is checked against a
live one by `make sandbox-check` rather than mocked here.
"""

import asyncio
import csv

import pytest

from analyst_agent.agent.session import QueryRecord, Session
from analyst_agent.agent.tools import code as tools
from analyst_agent.core.events import Event, emit
from analyst_agent.core.proc import Result
from analyst_agent.sandbox import scratch
from analyst_agent.transcript import ChatStore

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
    store = ChatStore(tmp_path / "analyst.db")
    await store.init()
    chat = await store.create("test", kind="question")
    result_id = await store.save_result(chat.id, "turn-1", COLUMNS, ROWS)

    built = Session(
        catalog=catalog,
        client=cube,
        grounding=None,
        store=store,
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
    """What the tools say. Collected here; one test below leaves `emit` alone instead."""
    captured: list[dict] = []
    monkeypatch.setattr(tools, "emit", lambda event: captured.append(event.model_dump()))
    return captured


def named(built: list, name: str):
    return next(t for t in built if t.name == name)


@pytest.fixture
def sandbox(monkeypatch):
    """The docker half replaced by a callable the test supplies. What a real container does with
    a real signal is `make sandbox-check`'s job."""
    killed: list[int] = []

    async def up():
        return Result(0, "analyst-sandbox", "")

    async def kill(pid: int):
        killed.append(pid)
        return Result(0, "", "")

    async def running():
        return "running"

    monkeypatch.setattr(tools.container, "ensure_up", up)
    monkeypatch.setattr(tools.container, "kill", kill)
    monkeypatch.setattr(tools.container, "state", running)

    def run(execute):
        monkeypatch.setattr(tools.container, "execute", execute)
        return killed

    return run


# --- the surface --------------------------------------------------------------


async def test_the_toolset_is_two_tools(session, data_dir):
    built = tools.build_code_tools(session)
    assert {t.name for t in built} == {"export_result", "run_python"}
    # The workspace is made when the tools are, not by `docker run`: a bind mount whose host
    # path is missing is not an error — docker creates an empty directory there.
    assert (data_dir / "workspaces" / CHAT).is_dir()


async def test_a_session_with_no_chat_still_gets_a_workspace(catalog, cube, data_dir):
    """Only tests build one. It must not land on the `workspaces` root itself, which is the
    directory the container mounts."""
    tools.build_code_tools(Session(catalog=catalog, client=cube, grounding=None))
    assert (data_dir / "workspaces" / "scratch").is_dir()


# --- export_result ------------------------------------------------------------


async def test_exporting_writes_the_rows_with_member_ids_as_the_header(session, data_dir):
    """Member ids, not labels: a script refers to `sales.invoice_total`, which is what the agent
    knows the column by. The download route writes labels, because a person reads that file."""
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "2 rows" in out and "sales.invoice_total" in out

    written = (data_dir / "workspaces" / CHAT / "result.csv").read_text()
    rows = list(csv.reader(written.splitlines()))
    assert rows[0] == ["sales.month", "sales.invoice_total"]
    assert [r[1] for r in rows[1:]] == ["100", "125"]


async def test_exporting_takes_a_filename(session, data_dir):
    await named(tools.build_code_tools(session), "export_result").ainvoke(
        {"filename": "data/monthly.csv"}
    )
    assert (data_dir / "workspaces" / CHAT / "data" / "monthly.csv").is_file()


async def test_exporting_outside_the_workspace_is_refused(session, data_dir):
    secret = data_dir / "analyst.db"
    before = secret.read_bytes()
    out = await named(tools.build_code_tools(session), "export_result").ainvoke(
        {"filename": "../../analyst.db"}
    )
    assert "outside the workspace" in out
    assert secret.read_bytes() == before


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


async def test_a_script_is_saved_before_it_runs_and_numbered(data_dir):
    """Saved rather than piped to `python -c`, so a computed figure's provenance is a file that
    outlives the transcript — and a second attempt does not overwrite the first."""
    assert scratch.save_script(CHAT, "print(1)\n") == "scripts/run-001.py"
    assert scratch.save_script(CHAT, "print(2)\n") == "scripts/run-002.py"
    saved = data_dir / "workspaces" / CHAT / "scripts"
    assert (saved / "run-001.py").read_text() == "print(1)\n"
    assert sorted(p.name for p in saved.glob("*.py")) == ["run-001.py", "run-002.py"]


# --- stopping a script --------------------------------------------------------


async def test_pressing_stop_terms_the_running_script(session, sandbox, data_dir):
    """The defect this closes: the turn loop only tests the flag between stream chunks, and an
    exec is one `await` no chunk interrupts. So the tool watches the flag itself."""
    session.cancel = asyncio.Event()

    async def execute(chat_id, command, timeout=None, on_output=None):
        # What `echo $$` does in the real command, before anything else.
        (data_dir / "workspaces" / CHAT / ".logs" / "run-001.pid").write_text("4242\n")
        session.cancel.set()
        await asyncio.sleep(0.1)
        return Result(143, "", "")

    killed = sandbox(execute)
    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "pass"})

    assert killed == [4242]
    assert "stopped by the user" in out


async def test_a_script_that_finishes_first_is_never_killed(session, sandbox):
    session.cancel = asyncio.Event()

    async def execute(chat_id, command, timeout=None, on_output=None):
        return Result(0, "done\n", "")

    killed = sandbox(execute)
    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "pass"})

    assert killed == []
    assert "stopped" not in out


async def test_nothing_runs_at_all_once_the_turn_has_been_stopped(session, sandbox):
    session.cancel = asyncio.Event()
    session.cancel.set()
    ran = []
    sandbox(lambda *a, **k: ran.append(a))

    out = await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "pass"})
    assert ran == []
    assert "stopped this turn" in out


# --- what a running script says ----------------------------------------------


async def test_output_is_streamed_as_it_arrives_and_carries_nothing_but_a_delta(
    session, sandbox, events
):
    async def execute(chat_id, command, timeout=None, on_output=None):
        on_output("half a line")
        await asyncio.sleep(tools.STREAM_INTERVAL_S)
        on_output(" and the rest\n")
        return Result(0, "half a line and the rest\n", "")

    sandbox(execute)
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

    async def execute(chat_id, command, timeout=None, on_output=None):
        for _ in range(50):
            on_output("x" * 10)
        return Result(0, "x" * 500, "")

    sandbox(execute)
    await named(tools.build_code_tools(session), "run_python").ainvoke({"script": "print(1)"})

    streamed = "".join(e["data"]["delta"] for e in events if e["type"] == "output")
    assert 0 < len(streamed) <= 30, "the live view kept going past its limit"


# --- the turn's budget --------------------------------------------------------


async def test_a_turn_that_has_spent_its_output_budget_refuses_the_next_script(
    session, sandbox, monkeypatch
):
    """Per-call caps already exist; this is the one that stops forty of them adding up."""
    monkeypatch.setattr(tools, "TURN_OUTPUT", 5)

    async def execute(chat_id, command, timeout=None, on_output=None):
        return Result(0, "hello world\n", "")

    sandbox(execute)
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


async def test_the_tools_run_with_the_real_emit_left_alone(session, data_dir):
    """Every other test here monkeypatches `emit`, which is exactly what once hid a TypeError
    raised while an event was being built. Outside a graph the real `emit` is a no-op, so this
    drives a tool through it."""
    assert emit(Event(type="tool_start", data={"tool": "x"})) is None
    out = await named(tools.build_code_tools(session), "export_result").ainvoke({})
    assert "2 rows" in out
    assert (data_dir / "workspaces" / CHAT / "result.csv").is_file()
