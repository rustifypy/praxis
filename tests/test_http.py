"""The HTTP surface: auth, the chat and result routes, sources and dashboards.

What is asserted is what only exists at this layer, since the pure functions underneath are
covered on their own: the token middleware, the status codes, the route shapes, and the
derived fields the browser is forbidden from recomputing.

No containers and no model calls, per the repo's rule. `app.state` is populated directly
rather than by running the lifespan, because the real one opens `~/.analyst` — the databases
here are all under tmp_path. Nothing needs a container stubbed any more: the portal does not
run the deployment, so "is it up" is an HTTP probe and respx answers it.
"""

import httpx
import pytest

from analyst_agent.core import jobs
from analyst_agent.core.config import settings
from analyst_agent.core.deployment import Deployment
from analyst_agent.web import server, state

SHOP = Deployment(
    store_url="http://localhost:8500",
    store_token="w" * 40,
    api_secret="s" * 40,
    description="Orders.",
    driver="postgres",
    credentials={"CUBEJS_DB_NAME": "shop"},
)

STORED = Deployment(
    api_secret="s" * 40,
    store_url="http://store:8500",
    store_token="writetok",
    driver="postgres",
)
"""A deployment with an address, for the routes that read the model rather than query it."""


@pytest.fixture
async def api(tmp_path, monkeypatch, cube):
    """The app with its state wired to tmp databases and a fake Cube."""
    from analyst_agent.core.deployment import DeploymentStore
    from analyst_agent.dashboards.store import DashboardStore
    from analyst_agent.transcript import ChatStore

    # Every store defaults to `settings.db_path`, so pointing the setting is what keeps these
    # routes off the real ~/.analyst.
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))

    chats = ChatStore(tmp_path / "analyst.db")
    await chats.init()
    boards = DashboardStore(tmp_path / "analyst.db")
    await boards.init()
    sources = DeploymentStore(tmp_path / "analyst.db")
    await sources.init()

    server.app.state.store = chats
    server.app.state.dashboards = boards
    server.app.state.sources = sources
    server.app.state.jobs = jobs.Runner()
    server.app.state.health = None
    server.app.state.cube = cube
    server.app.state.cube_key = None
    # The routes ask for the session through `state.cube`, which rebuilds it when the row's
    # address moves. Patching the accessor is what hands them the fake instead.
    monkeypatch.setattr(state, "cube", _handing_over(cube))
    server.app.state.cancels = {}
    server.app.state.approvals = {}
    server.app.state.always = set()
    server.app.state.saver = None

    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={server.COOKIE: server.TOKEN},
    ) as client:
        client.stores = (chats, boards, sources)  # type: ignore[attr-defined]
        yield client


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


class FakeSandbox:
    """The sandbox service, as the routes see it. Its own suite covers what a real one does; what
    matters here is that these routes proxy rather than touch a filesystem."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.uploaded: list[tuple[str, bytes]] = []
        self.deleted: list[str] = []
        self.forgotten: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def _send(self, method, path, **kwargs):
        if path.startswith("/uploads/"):
            name, data = kwargs["files"]["file"]
            self.uploaded.append((name, data))
            stored = f"uploads/{name}"
            self.files[stored] = data
            return {
                "path": stored,
                "profile": {
                    "path": stored,
                    "rows": 2,
                    "columns": [
                        {"name": "a", "type": "BIGINT"},
                        {"name": "b", "type": "BIGINT"},
                    ],
                },
            }
        if path.startswith("/files/") and method == "DELETE":
            self.deleted.append(path.split("/", 3)[3])
            return {}
        if path.startswith("/workspace/"):
            self.forgotten.append(path.rsplit("/", 1)[1])
            return {}
        return {}

    async def read(self, chat_id, path):
        from analyst_agent.sandbox import SandboxError

        if path not in self.files:
            raise SandboxError(f"no file at {path!r}", status=404)
        return self.files[path], path.rsplit("/", 1)[-1]

    async def delete(self, chat_id, path):
        await self._send("DELETE", f"/files/{chat_id}/{path}")

    async def forget(self, chat_id):
        await self._send("DELETE", f"/workspace/{chat_id}")

    async def listing(self, chat_id):
        return sorted(self.files), sorted(p for p in self.files if p.startswith("uploads/"))


@pytest.fixture
def fake_sandbox(monkeypatch):
    from analyst_agent.web import files as files_routes

    fake = FakeSandbox()

    async def opened(*args, **kwargs):
        return fake

    monkeypatch.setattr(files_routes, "open_sandbox", opened)
    monkeypatch.setattr(server, "open_sandbox", opened)
    return fake


@pytest.fixture
def no_sandbox(monkeypatch):
    """A deployment with none configured — which is every deployment until one is run."""
    from analyst_agent.sandbox import SandboxError
    from analyst_agent.web import files as files_routes

    async def refused(*args, **kwargs):
        raise SandboxError("no sandbox is configured for this deployment")

    monkeypatch.setattr(files_routes, "open_sandbox", refused)
    monkeypatch.setattr(server, "open_sandbox", refused)


async def test_deleting_a_chat_asks_the_sandbox_to_drop_its_workspace(api, fake_sandbox):
    """The workspace is the sandbox's, so this is a request rather than an `rmtree` — and a
    best-effort one, since a transcript that is gone must not leave the chat undeletable."""
    chat = (await api.post("/api/chats", json={})).json()
    assert (await api.delete(f"/api/chats/{chat['id']}")).status_code == 200
    assert fake_sandbox.forgotten == [chat["id"]]


async def test_a_chat_still_deletes_with_the_sandbox_down(api, no_sandbox):
    chat = (await api.post("/api/chats", json={})).json()
    assert (await api.delete(f"/api/chats/{chat['id']}")).status_code == 200
    assert (await api.get("/api/chats")).json() == []


# --- files in and out of a workspace -----------------------------------------
# The workspace lives in the sandbox service, so these routes are a proxy: what is asserted here
# is the proxying and the statuses, and the path guard is covered by the service's own suite.


async def upload(api, chat_id: str, name: str, body: bytes = b"a,b\n1,2\n"):
    return await api.post(f"/api/chats/{chat_id}/files", files={"file": (name, body, "text/csv")})


async def test_an_upload_is_handed_over_and_its_shape_reported_back(api, fake_sandbox):
    """Profiled by the service on arrival, so this is one round trip and the columns are known
    before the first question about the file."""
    chat = (await api.post("/api/chats", json={})).json()
    body = (await upload(api, chat["id"], "orders.csv")).json()

    assert body["path"] == "uploads/orders.csv"
    assert body["rows"] == 2 and body["columns"] == 2
    assert fake_sandbox.uploaded == [("orders.csv", b"a,b\n1,2\n")]


async def test_a_file_over_the_cap_is_refused_before_it_is_handed_over(api, fake_sandbox):
    """Content-Length is the cheap refusal and the running total is the real one — a chunked
    upload sends no length at all."""
    from analyst_agent.web import files

    chat = (await api.post("/api/chats", json={})).json()
    response = await upload(api, chat["id"], "big.csv", b"x" * (files.MAX_BYTES + 1))
    assert response.status_code == 413
    assert "100 MB" in response.text
    assert fake_sandbox.uploaded == []


async def test_uploading_to_an_unknown_chat_is_a_404(api, fake_sandbox):
    assert (await upload(api, "ghost", "orders.csv")).status_code == 404


async def test_no_sandbox_is_a_503_rather_than_a_500(api, no_sandbox):
    """A deployment without one is a working deployment that cannot run Python, so the status
    has to say "not here" rather than "broken"."""
    chat = (await api.post("/api/chats", json={})).json()
    assert (await upload(api, chat["id"], "orders.csv")).status_code == 503


async def test_a_written_file_comes_back_out(api, fake_sandbox):
    fake_sandbox.files["chart.png"] = b"\x89PNG\r\n"
    chat = (await api.post("/api/chats", json={})).json()

    response = await api.get(f"/api/chats/{chat['id']}/files/chart.png")
    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n"
    # Inline, so an image renders in the transcript rather than downloading.
    assert "attachment" not in response.headers.get("content-disposition", "")


async def test_a_csv_is_a_download_and_a_missing_file_is_the_services_answer(api, fake_sandbox):
    fake_sandbox.files["summary.csv"] = b"a,b\n1,2\n"
    chat = (await api.post("/api/chats", json={})).json()

    served = await api.get(f"/api/chats/{chat['id']}/files/summary.csv")
    assert "attachment" in served.headers["content-disposition"]
    assert (await api.get(f"/api/chats/{chat['id']}/files/nope.csv")).status_code == 404


async def test_taking_a_file_back_reaches_the_sandbox(api, fake_sandbox):
    """The composer's ✕ deletes the file rather than only the chip: the prompt is built from
    `uploads/` every turn, so a chip removed while the file stayed would keep describing it."""
    fake_sandbox.files["uploads/orders.csv"] = b"a,b\n"
    chat = (await api.post("/api/chats", json={})).json()
    assert (
        await api.delete(f"/api/chats/{chat['id']}/files/uploads/orders.csv")
    ).status_code == 200
    assert fake_sandbox.deleted == ["uploads/orders.csv"]


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


def _handing_over(cube):
    async def accessor(_app):
        return cube

    return accessor
