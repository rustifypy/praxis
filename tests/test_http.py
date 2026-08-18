"""The HTTP surface: auth, the chat and result routes, sources and dashboards.

What is asserted is what only exists at this layer, since the pure functions underneath are
covered on their own: the token middleware, the status codes, the route shapes, and the
derived fields the browser is forbidden from recomputing.

No containers and no model calls, per the repo's rule. `app.state` is populated directly
rather than by running the lifespan, because the real one opens `~/.analyst` — the databases
here are all under tmp_path. `docker.state` is stubbed: asking this machine what is running
would make the same test answer differently on a laptop and in CI.
"""

import httpx
import pytest

from analyst_agent.core import jobs
from analyst_agent.core.config import settings
from analyst_agent.sources import docker
from analyst_agent.sources.registry import Source
from analyst_agent.web import server

SHOP = Source(description="Orders.", driver="postgres", credentials={"CUBEJS_DB_NAME": "shop"})


@pytest.fixture
async def api(tmp_path, monkeypatch, cube):
    """The app with its state wired to tmp databases and a fake Cube."""
    from analyst_agent.dashboards.store import DashboardStore
    from analyst_agent.sources.store import SourceStore
    from analyst_agent.transcript import ChatStore

    # Every store defaults to `settings.db_path`, so pointing the setting is what keeps these
    # routes off the real ~/.analyst.
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    chats = ChatStore(tmp_path / "analyst.db")
    await chats.init()
    boards = DashboardStore(tmp_path / "analyst.db")
    await boards.init()
    sources = SourceStore(tmp_path / "analyst.db")
    await sources.init()

    server.app.state.store = chats
    server.app.state.dashboards = boards
    server.app.state.sources = sources
    server.app.state.jobs = jobs.Runner()
    server.app.state.health = None
    server.app.state.cube = cube
    server.app.state.cancels = {}
    server.app.state.approvals = {}
    server.app.state.always = set()
    server.app.state.saver = None

    # Nothing is running, so the source reads as stopped — deterministic, and the state the
    # `serving_state` assertions below care about.
    monkeypatch.setattr(docker, "state", _no_container)

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={server.COOKIE: server.TOKEN},
    ) as client:
        client.stores = (chats, boards, sources)  # type: ignore[attr-defined]
        yield client


async def _no_container() -> str:
    return ""


# --- the token middleware ----------------------------------------------------


async def test_healthz_needs_no_token():
    """The one route the middleware lets past, so a launcher can poll for readiness."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    ) as bare:
        assert (await bare.get("/api/healthz")).status_code == 200


async def test_every_other_route_is_refused_without_the_token():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    ) as bare:
        for path in ("/api/chats", "/api/catalog", "/api/source", "/api/dashboards", "/"):
            assert (await bare.get(path)).status_code == 401, path


async def test_a_wrong_token_is_refused():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    ) as bare:
        assert (await bare.get("/api/chats?token=not-the-token")).status_code == 401


async def test_a_token_in_the_query_string_is_moved_into_a_cookie(api):
    """So a reload and an EventSource carry it implicitly — neither can set a header."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    ) as bare:
        response = await bare.get(f"/api/chats?token={server.TOKEN}")
    assert response.status_code == 200
    assert response.cookies[server.COOKIE] == server.TOKEN


# --- chats -------------------------------------------------------------------


async def test_a_chat_is_created_listed_renamed_and_deleted(api):
    assert (await api.get("/api/chats")).json() == []

    created = (await api.post("/api/chats", json={"title": "Revenue"})).json()
    chat_id = created["id"]
    assert [c["title"] for c in (await api.get("/api/chats")).json()] == ["Revenue"]

    await api.patch(f"/api/chats/{chat_id}", json={"title": "Revenue by month"})
    assert (await api.get("/api/chats")).json()[0]["title"] == "Revenue by month"

    assert (await api.delete(f"/api/chats/{chat_id}")).status_code == 200
    assert (await api.get("/api/chats")).json() == []


async def test_a_replayed_transcript_is_the_event_log(api):
    """`events` is the whole contract with the browser, so the route returns it verbatim."""
    chat = (await api.post("/api/chats", json={})).json()
    assert (await api.get(f"/api/chats/{chat['id']}/events")).json() == []


async def test_stopping_a_chat_with_nothing_in_flight_is_not_an_error(api):
    chat = (await api.post("/api/chats", json={})).json()
    assert (await api.post(f"/api/chats/{chat['id']}/stop")).status_code == 200


async def test_approving_when_nothing_is_waiting_is_a_404_rather_than_a_silent_ok(api):
    """A click that lands after the turn ended must not read as having approved anything."""
    chat = (await api.post("/api/chats", json={})).json()
    response = await api.post(f"/api/chats/{chat['id']}/approve", json={"decision": "yes"})
    assert response.status_code == 404


async def test_a_decision_reaches_the_waiting_script(api):
    import asyncio

    chat = (await api.post("/api/chats", json={})).json()
    queue = asyncio.Queue()
    server.app.state.approvals[chat["id"]] = queue

    assert (
        await api.post(f"/api/chats/{chat['id']}/approve", json={"decision": "always"})
    ).status_code == 200
    assert queue.get_nowait() == "always"
    # And the chat is not asked again, which is what "always" means past this turn.
    assert chat["id"] in server.app.state.always


async def test_an_invented_decision_is_refused_by_the_schema(api):
    """Before the handler, so a typo cannot land as a "yes" by falling through a comparison."""
    chat = (await api.post("/api/chats", json={})).json()
    response = await api.post(f"/api/chats/{chat['id']}/approve", json={"decision": "maybe"})
    assert response.status_code == 422


async def test_deleting_a_chat_takes_its_workspace_with_it(api, tmp_path):
    """The sandbox container is shared between chats, so it survives; the workspace directory is
    this chat's alone and goes with it."""
    from analyst_agent.sandbox import scratch

    chat = (await api.post("/api/chats", json={})).json()
    workspace = scratch.ensure(chat["id"])
    (workspace / "result.csv").write_text("a,b\n")
    assert workspace.is_relative_to(tmp_path)

    assert (await api.delete(f"/api/chats/{chat['id']}")).status_code == 200
    assert not workspace.exists()


# --- files in and out of a workspace -----------------------------------------


@pytest.fixture
def unprofiled(monkeypatch):
    """Profiling runs a script in the sandbox, and this suite starts no containers. What the
    route does with the answer is what is asserted here; the profile itself is its own test."""
    from analyst_agent.sandbox.profile import Profile
    from analyst_agent.web import files

    async def fake(chat_id: str, path: str) -> Profile:
        return Profile(path=path, bytes=4, rows=2, note="not profiled")

    monkeypatch.setattr(files.profile, "of", fake)


async def upload(api, chat_id: str, name: str, body: bytes = b"a,b\n1,2\n"):
    return await api.post(f"/api/chats/{chat_id}/files", files={"file": (name, body, "text/csv")})


async def test_an_uploaded_file_lands_in_the_chats_workspace(api, unprofiled, tmp_path):
    chat = (await api.post("/api/chats", json={})).json()
    body = (await upload(api, chat["id"], "orders.csv")).json()

    assert body["path"] == "uploads/orders.csv"
    written = tmp_path / "workspaces" / chat["id"] / "uploads" / "orders.csv"
    assert written.read_bytes() == b"a,b\n1,2\n"


async def test_a_browser_filename_cannot_choose_where_the_file_goes(api, unprofiled, tmp_path):
    """The name is untrusted input. `save_upload` takes the basename and `scratch.resolve`
    checks the resolved target, which is the one check that covers `..`, an absolute path and a
    symlink."""
    chat = (await api.post("/api/chats", json={})).json()
    secret = tmp_path / "analyst.db"
    before = secret.read_bytes()

    for name in ("../../analyst.db", "/etc/passwd", "a/../../b.csv"):
        body = (await upload(api, chat["id"], name)).json()
        assert body["path"].startswith("uploads/")
        assert "/" not in body["path"][len("uploads/") :]
    assert secret.read_bytes() == before


async def test_a_dotted_filename_does_not_land_invisible(api, unprofiled):
    """`scratch.listing` skips dotted paths, so `.secret.csv` would be on disk and invisible to
    the agent, to the snapshot and to the profiler — an upload that appears to have worked."""
    chat = (await api.post("/api/chats", json={})).json()
    body = (await upload(api, chat["id"], ".secret.csv")).json()
    assert body["path"] == "uploads/secret.csv"


async def test_uploading_the_same_name_twice_keeps_both(api, unprofiled):
    """A figure in an earlier answer came from the earlier file. Numbered, like a saved script."""
    chat = (await api.post("/api/chats", json={})).json()
    first = (await upload(api, chat["id"], "orders.csv")).json()
    second = (await upload(api, chat["id"], "orders.csv")).json()
    assert (first["path"], second["path"]) == ("uploads/orders.csv", "uploads/orders-2.csv")


async def test_a_file_over_the_cap_is_refused_with_the_limit_named(api, unprofiled):
    from analyst_agent.web import files

    chat = (await api.post("/api/chats", json={})).json()
    response = await upload(api, chat["id"], "big.csv", b"x" * (files.MAX_BYTES + 1))
    assert response.status_code == 413
    assert "100 MB" in response.json()["detail"]


async def test_uploading_to_an_unknown_chat_is_a_404(api, unprofiled):
    assert (await upload(api, "ghost", "orders.csv")).status_code == 404


async def test_a_written_file_comes_back_out(api):
    """The workspace is the store — there is no table behind this, and the route is a read
    through the same `resolve` the tools use."""
    from analyst_agent.sandbox import scratch

    chat = (await api.post("/api/chats", json={})).json()
    (scratch.ensure(chat["id"]) / "chart.png").write_bytes(b"\x89PNG\r\n")

    response = await api.get(f"/api/chats/{chat['id']}/files/chart.png")
    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n"
    assert response.headers["content-type"] == "image/png"
    # Inline, so the transcript can render it as an image rather than downloading it.
    assert "content-disposition" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_csv_is_a_download_and_a_missing_file_is_a_404_not_a_400(api):
    from analyst_agent.sandbox import scratch

    chat = (await api.post("/api/chats", json={})).json()
    (scratch.ensure(chat["id"]) / "summary.csv").write_text("a,b\n")

    served = await api.get(f"/api/chats/{chat['id']}/files/summary.csv")
    assert "attachment" in served.headers["content-disposition"]

    assert (await api.get(f"/api/chats/{chat['id']}/files/nope.csv")).status_code == 404
    # Percent-encoded, because every client collapses a literal `../` before it is sent — which
    # is exactly why the guard cannot be the URL's shape. `resolve` checks the resolved target.
    escape = await api.get(f"/api/chats/{chat['id']}/files/%2e%2e%2f%2e%2e%2fanalyst.db")
    assert escape.status_code == 400
    assert "outside the workspace" in escape.json()["detail"]


async def test_a_file_can_be_taken_back_before_the_question_is_sent(api, unprofiled):
    """The composer's ✕. The prompt is built from the directory, so dropping the chip without
    dropping the file would leave the agent still holding it."""
    from analyst_agent.sandbox import profile, scratch

    chat = (await api.post("/api/chats", json={})).json()
    path = (await upload(api, chat["id"], "orders.csv")).json()["path"]
    assert scratch.uploaded(chat["id"]) == [path]

    assert (await api.delete(f"/api/chats/{chat['id']}/files/{path}")).status_code == 200
    assert scratch.uploaded(chat["id"]) == []
    assert profile.saved(chat["id"]) == []
    assert (await api.delete(f"/api/chats/{chat['id']}/files/{path}")).status_code == 404


async def test_a_dotted_filename_does_not_swallow_the_download_route(api):
    """`{path:path}` under a chat, and the fixed segments above it still match first."""
    routes = {getattr(r, "path", "") for r in server.app.routes}
    assert "/api/chats/{chat_id}/events" in routes
    chat = (await api.post("/api/chats", json={})).json()
    assert (await api.get(f"/api/chats/{chat['id']}/events")).json() == []


# --- results: the rows the event does not carry ------------------------------

COLUMNS = [
    {
        "id": "orders.status",
        "label": "Status",
        "kind": "dimension",
        "type": "string",
        "format": "",
        "align": "left",
    },
    {
        "id": "orders.count",
        "label": "Orders",
        "kind": "measure",
        "type": "number",
        "format": "",
        "align": "right",
    },
]
ROWS = [{"orders.status": f"s{i}", "orders.count": i} for i in range(5)]


async def test_an_unknown_result_is_a_404(api):
    assert (await api.get("/api/results/nope")).status_code == 404
    assert (await api.get("/api/results/nope/csv")).status_code == 404


async def test_a_result_is_paged_by_offset_and_limit(api):
    chats, _, _ = api.stores
    chat = await chats.create("c")
    result_id = await chats.save_result(chat.id, "turn-1", COLUMNS, ROWS)

    body = (await api.get(f"/api/results/{result_id}?offset=1&limit=2")).json()
    assert [r["orders.count"] for r in body["rows"]] == [1, 2]
    # The count is the whole set, not the page — it is what "showing 2 of 5" is built from.
    assert body["row_count"] == 5
    assert body["offset"] == 1


async def test_the_csv_header_is_the_stored_header_not_the_row_keys(api):
    """Cube returns a grouped time dimension under two keys, so `rows[0]` would invent a
    column. The labels are what a person reads in a spreadsheet."""
    chats, _, _ = api.stores
    chat = await chats.create("c")
    result_id = await chats.save_result(chat.id, "turn-1", COLUMNS, ROWS[:2])

    response = await api.get(f"/api/results/{result_id}/csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == "Status,Orders"


async def test_a_dotted_result_id_does_not_swallow_the_csv_route(api):
    """`/api/results/{id}.csv` would match the paging route with id "abc.csv", which is why
    the CSV route is a segment. This asserts the shape rather than the bug."""
    assert (await api.get("/api/results/abc.csv")).status_code == 404
    routes = {getattr(r, "path", "") for r in server.app.routes}
    assert "/api/results/{result_id}/csv" in routes


# --- the served catalog ------------------------------------------------------


async def test_the_catalog_route_reports_what_the_session_serves(api, catalog):
    body = (await api.get("/api/catalog")).json()
    names = {e["name"] for e in body["entities"]}
    assert names == {e.name for e in catalog.entities}
    assert next(e for e in body["entities"] if e["name"] == "revenue")["view"] is True


# --- the data source ---------------------------------------------------------


async def test_the_source_is_null_before_a_database_is_connected(api):
    assert (await api.get("/api/source")).json() is None


async def test_a_stopped_container_says_so_rather_than_reporting_no_model(api):
    """`serving` is null both for a stopped container and for a model Cube will not compile, so
    the row has to say which — the browser is given the answer rather than guessing."""
    _, _, sources = api.stores
    await sources.save(SHOP)

    row = (await api.get("/api/source")).json()
    assert row["serving"] is None
    assert row["serving_state"] == "stopped"
    assert row["running"] is False
    assert "CUBEJS_DB_NAME" in row["credential_keys"]
    assert row["label"] == "shop"


async def test_the_source_carries_the_stage_rail_and_the_next_stage(api):
    _, _, sources = api.stores
    await sources.save(SHOP)
    await sources.save_step("connect", {"status": "done"})

    row = (await api.get("/api/source")).json()
    assert [s["name"] for s in row["stages"]] == ["connect", "read", "cubes", "describe", "views"]
    assert row["next_stage"] == "read"
    assert next(s for s in row["stages"] if s["name"] == "describe")["blocked_by"] == "cubes"


async def test_no_credential_value_ever_leaves_the_process(api):
    _, _, sources = api.stores
    await sources.save(Source(driver="postgres", credentials={"CUBEJS_DB_PASS": "hunter2"}))
    assert "hunter2" not in (await api.get("/api/source")).text


async def test_the_driver_form_ships_what_is_required_and_no_flag_to_re_derive_it(api):
    """The form filtered booleans where the server filters fixed values, so `Connect` could
    enable on a body `POST /source` would reject."""
    drivers = {d["key"]: d for d in (await api.get("/api/drivers")).json()["drivers"]}
    postgres = drivers["postgres"]
    assert "CUBEJS_DB_NAME" in postgres["required_envs"]
    assert "CUBEJS_DB_SSL" not in postgres["required_envs"]
    assert all("required" not in f for f in postgres["fields"]), (
        "a per-field flag is a second way to answer the same question"
    )
    # A fixed field is not offered at all, and is not in the required list either.
    databricks = drivers["databricks-jdbc"]
    offered = {f["env"] for f in databricks["fields"]}
    assert "CUBEJS_DB_DATABRICKS_ACCEPT_POLICY" not in offered | set(databricks["required_envs"])


async def test_connecting_without_a_required_field_is_refused_by_name(api):
    response = await api.post(
        "/api/source",
        json={
            "description": "",
            "driver": "postgres",
            "values": {"CUBEJS_DB_HOST": "host.docker.internal"},
        },
    )
    assert response.status_code == 400
    assert "CUBEJS_DB_NAME" in response.text


async def test_connecting_replaces_whatever_was_there(api):
    """One Cube deployment, so there is nothing to add to — and the steps go with the old
    database, because they describe it."""
    _, _, sources = api.stores
    await sources.save(SHOP)
    await sources.save_step("connect", {"status": "done"})

    body = {
        "description": "",
        "driver": "postgres",
        "values": {
            "CUBEJS_DB_HOST": "h",
            "CUBEJS_DB_PORT": "5432",
            "CUBEJS_DB_NAME": "other",
            "CUBEJS_DB_USER": "u",
            "CUBEJS_DB_PASS": "p",
        },
    }
    row = (await api.post("/api/source", json=body)).json()
    assert row["label"] == "other"
    assert row["steps"] == {}


async def test_an_unknown_action_is_a_404_from_the_route(api):
    """The route checks the stage list itself so the answer is 404 rather than a failed job."""
    _, _, sources = api.stores
    await sources.save(SHOP)
    response = await api.post("/api/source/actions/enrich", json={})
    assert response.status_code == 404
    assert "enrich" in response.text


async def test_reading_the_source_before_one_exists_is_a_404(api):
    assert (await api.get("/api/source/model")).status_code == 200
    assert (await api.post("/api/source/check")).status_code == 404


# --- dashboards --------------------------------------------------------------


async def test_an_unknown_dashboard_reads_as_404_but_refreshes_as_a_failed_run(api):
    """Not an inconsistency to fix here: `refresh._open` returns an outcome rather than raising,
    so that a check cannot apply to `validate` and not to `refresh`. The button renders the
    outcome, so a deleted board reports `failed` instead of throwing."""
    assert (await api.get("/api/dashboards/nope")).status_code == 404
    response = await api.post("/api/dashboards/nope/refresh")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "nope" in response.json()["error"]


async def test_refreshing_an_empty_board_is_refused_not_ok(api):
    """`validate` calls an empty board ok and `refresh` calls it refused, deliberately — there
    is nothing to run. The status is what the button reports."""
    _, boards, _ = api.stores
    board = await boards.create("Weekly")
    body = (await api.post(f"/api/dashboards/{board.id}/refresh")).json()
    assert body["ok"] is False
    assert body["status"] == "refused"
    assert body["tiles"] == []


async def test_a_dashboard_is_listed_renamed_and_deleted(api):
    _, boards, _ = api.stores
    board = await boards.create("Weekly")

    listed = (await api.get("/api/dashboards")).json()
    assert [b["title"] for b in listed] == ["Weekly"]

    await api.patch(f"/api/dashboards/{board.id}", json={"title": "Monthly"})
    assert (await api.get(f"/api/dashboards/{board.id}")).json()["title"] == "Monthly"

    assert (await api.delete(f"/api/dashboards/{board.id}")).status_code == 200
    assert (await api.get("/api/dashboards")).json() == []
