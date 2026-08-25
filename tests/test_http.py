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
from fastapi import HTTPException

from analyst_agent.api import server
from analyst_agent.core.deployment import Deployment
from analyst_agent.modelstore.client import Ref

SHOP = Deployment(
    store_url="http://localhost:8000",
    store_token="w" * 40,
    api_secret="s" * 40,
    description="Orders.",
    driver="postgres",
    credentials={"CUBEJS_DB_NAME": "shop"},
)

STORED = Deployment(
    api_secret="s" * 40,
    store_url="http://store:8000",
    store_token="writetok",
    driver="postgres",
)
"""A deployment with an address, for the routes that read the model rather than query it."""


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
        for path in (
            "/api/chats",
            "/api/ide/model/summary",
            "/api/onboarding/connection",
            "/api/dashboard",
            "/",
        ):
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

    async def upload(self, chat_id, filename, data):
        """The path and the profile in one answer, as the real client returns them."""
        self.uploaded.append((filename, data))
        stored = f"uploads/{filename}"
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

    async def _send(self, method, path, **kwargs):
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
    from analyst_agent.api.chat import controller as workspace_controller
    from analyst_agent.api.chat_management import controller as chat_controller

    fake = FakeSandbox()

    async def opened(*args, **kwargs):
        return fake

    monkeypatch.setattr(workspace_controller, "open_sandbox", opened)
    monkeypatch.setattr(chat_controller, "open_sandbox", opened)
    return fake


@pytest.fixture
def no_sandbox(monkeypatch):
    """A deployment with none configured — which is every deployment until one is run."""
    from analyst_agent.api.chat import controller as workspace_controller
    from analyst_agent.api.chat_management import controller as chat_controller
    from analyst_agent.sandbox import SandboxError

    async def refused(*args, **kwargs):
        raise SandboxError("no sandbox is configured for this deployment")

    monkeypatch.setattr(workspace_controller, "open_sandbox", refused)
    monkeypatch.setattr(chat_controller, "open_sandbox", refused)


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
    from analyst_agent.api.chat import controller as workspace_controller

    chat = (await api.post("/api/chats", json={})).json()
    over = b"x" * (workspace_controller.MAX_BYTES + 1)
    response = await upload(api, chat["id"], "big.csv", over)
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
    assert (await api.get("/api/dashboard/nope")).status_code == 404
    response = await api.post("/api/dashboard/nope/refresh")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "nope" in response.json()["error"]


async def test_refreshing_an_empty_board_is_refused_not_ok(api):
    """`validate` calls an empty board ok and `refresh` calls it refused, deliberately — there
    is nothing to run. The status is what the button reports."""
    _, boards, _ = api.stores
    board = await boards.create("Weekly")
    body = (await api.post(f"/api/dashboard/{board.id}/refresh")).json()
    assert body["ok"] is False
    assert body["status"] == "refused"
    assert body["tiles"] == []


async def test_a_dashboard_is_listed_renamed_and_deleted(api):
    _, boards, _ = api.stores
    board = await boards.create("Weekly")

    listed = (await api.get("/api/dashboard")).json()
    assert [b["title"] for b in listed] == ["Weekly"]

    await api.patch(f"/api/dashboard/{board.id}", json={"title": "Monthly"})
    assert (await api.get(f"/api/dashboard/{board.id}")).json()["title"] == "Monthly"

    assert (await api.delete(f"/api/dashboard/{board.id}")).status_code == 200
    assert (await api.get("/api/dashboard")).json() == []


# --- connecting --------------------------------------------------------------

CHINOOK = {
    "driver": "postgres",
    "description": "Chinook",
    "values": {
        "CUBEJS_DB_HOST": "host.docker.internal",
        "CUBEJS_DB_PORT": "5433",
        "CUBEJS_DB_NAME": "chinook",
        "CUBEJS_DB_USER": "analyst",
        "CUBEJS_DB_PASS": "chinook",
    },
}


@pytest.fixture
def answering(monkeypatch):
    """Both services reachable, so a test can get past step two of connecting. The addresses in
    these tests answer nothing, and `PUT /deployment` stores only what passed."""
    from analyst_agent.api.onboarding import reachable, routes

    async def reached(deployment, service, url):
        return reachable.Reached(True, "ok")

    monkeypatch.setattr(routes.reachable, "test", reached)


async def test_connecting_mints_every_secret_the_deployments_need(api):
    """All three, at connect and never again — they go into files someone runs elsewhere.

    Asserted through the route rather than on `render`, because the renderer's own tests hand
    it a populated `Deployment`: `sandbox_token` was a column nothing minted, and every one of
    them passed while the sandbox's file went out with `SANDBOX_TOKEN=`.
    """
    assert (await api.post("/api/onboarding/connection", json=CHINOOK)).status_code == 200

    _, _, sources = api.stores
    deployment = await sources.current()
    minted = {
        "api_secret": deployment.api_secret,
        "store_token": deployment.store_token,
        "sandbox_token": deployment.sandbox_token,
    }
    blank = [name for name, value in minted.items() if not value]
    assert not blank, f"minted nothing for {blank}"
    assert len(set(minted.values())) == 3, "a secret is reused across roles"


async def test_the_handover_is_a_download_per_service(api):
    """The compose files are the whole interface to whoever runs these, and a download rather
    than a body the page renders — there is nothing to do with one but run it somewhere."""
    await api.post("/api/onboarding/connection", json=CHINOOK)

    for service in ("semantic", "sandbox"):
        response = await api.get(f"/api/onboarding/deployment/{service}/compose")
        assert response.status_code == 200, service
        assert "attachment" in response.headers["content-disposition"]
        assert "docker-compose.yml" in response.headers["content-disposition"]
        # The guard that matters: an unresolved variable reads as a complete file and dies at
        # `up` with compose's message about an environment nobody was told to set. A doubled
        # `$` is the deliberate escape, since compose interpolates config content too.
        assert "${" not in response.text, service
        assert "$" not in response.text.replace("$$", ""), service

    assert (await api.get("/api/onboarding/deployment/nonsense/compose")).status_code == 404


async def test_nothing_is_running_after_connecting(api):
    """Connecting stores credentials; the addresses arrive later from whoever ran the compose
    file. So this state is neutral rather than unhealthy — a red dot on a deployment whose
    environment file has not been handed over yet is a bug report waiting to happen."""
    body = (await api.post("/api/onboarding/connection", json=CHINOOK)).json()
    assert body["deployed"] is False
    assert body["health"]["status"] == "not deployed"
    assert body["store_url"] == ""


async def test_both_addresses_are_not_finished_setup_without_a_model(api, answering):
    """Computed and not a column, because `CREATE TABLE IF NOT EXISTS` cannot add one. Setup now
    ends with a built model, so two answering addresses and an empty store is the state the
    wizard's last step is entered in — not the state it is finished in."""
    body = (await api.post("/api/onboarding/connection", json=CHINOOK)).json()
    assert (body["deployed"], body["onboarded"]) == (False, False)

    body = (
        await api.put("/api/onboarding/deployment", json={"store_url": "http://store:8000"})
    ).json()
    source = body["connection"]
    assert (source["deployed"], source["onboarded"]) == (True, False), "the sandbox is still owed"

    body = (
        await api.put("/api/onboarding/deployment", json={"sandbox_url": "http://sb:8600"})
    ).json()
    assert body["connection"]["onboarded"] is False, "the store holds no cubes yet"


async def test_an_address_that_does_not_answer_is_never_stored(api):
    """The check is what makes a typo recoverable: a stored-then-failed address is what makes a
    half-configured deployment look configured."""
    await api.post("/api/onboarding/connection", json=CHINOOK)

    body = (
        await api.put(
            "/api/onboarding/deployment", json={"store_url": "http://127.0.0.1:9/nothing-here"}
        )
    ).json()

    assert body["checked"]["semantic"]["ok"] is False
    assert body["connection"]["store_url"] == ""
    assert body["connection"]["deployed"] is False


async def test_editing_the_connection_discards_nothing(api):
    """A changed host may be the same database moved and a changed name may expose the same
    tables, so which happened is not derivable from the values. Guessing it wrong either way is
    worse than not guessing: `POST /source/reset` is the explicit answer."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    _, _, sources = api.stores
    await sources.save_step("cubes", {"status": "done"})
    await sources.save_state({"introspection": {"public": ["invoice"]}})

    await api.patch(
        "/api/onboarding/connection", json={"values": {"CUBEJS_DB_NAME": "somewhere-else"}}
    )

    assert "cubes" in await sources.steps()
    assert await sources.state() != {}


async def test_there_is_no_way_to_discard_what_was_built(api):
    """It existed for re-pointing a connection at a different database, which is re-onboarding
    now — and a rebuild runs every stage from `read`, so clearing the records first bought
    nothing."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    assert (await api.delete("/api/onboarding/pipeline")).status_code == 404


async def test_a_variable_that_is_not_a_warehouse_setting_is_refused(api):
    """The one rule about a name, and what keeps arbitrary keys safe: everything else the
    compose file sets is CUBEJS_ without the DB, so a typed key cannot overwrite the API
    secret or dev mode."""
    await api.post("/api/onboarding/connection", json=CHINOOK)

    refused = await api.patch(
        "/api/onboarding/connection", json={"values": {"CUBEJS_API_SECRET": "mine"}}
    )

    assert refused.status_code == 400
    assert "CUBEJS_API_SECRET" in refused.json()["detail"]


async def test_a_connection_that_cannot_open_is_refused_while_the_form_is_still_up(api):
    """The form asks for these and will not submit without them; this is the same rule on the
    near side of HTTP. Stored instead, it becomes a compose file that starts a deployment which
    then refuses every query — a failure two screens away from the box that caused it."""
    refused = await api.post(
        "/api/onboarding/connection",
        json={"driver": "postgres", "values": {"CUBEJS_DB_HOST": "db.internal"}},
    )

    assert refused.status_code == 400
    detail = refused.json()["detail"]
    assert "a database" in detail and "a user" in detail
    assert "CUBEJS_DB" not in detail, "a person reads this, and they typed into a box called User"

    _, _, sources = api.stores
    assert await sources.current() is None, "a connection that cannot open was stored anyway"


async def test_athenas_settings_are_aws_ones_and_still_accepted(api):
    """Athena is why the name rule allows a second prefix: its settings are AWS ones rather than
    database ones, so a single-prefix rule left the one warehouse on the list impossible to
    configure at all."""
    stored = await api.post(
        "/api/onboarding/connection",
        json={
            "driver": "athena",
            "values": {
                "CUBEJS_AWS_KEY": "AKIA",
                "CUBEJS_AWS_SECRET": "shh",
                "CUBEJS_AWS_REGION": "us-east-1",
                "CUBEJS_AWS_S3_OUTPUT_LOCATION": "s3://results/",
            },
        },
    )

    assert stored.status_code == 200, stored.text
    _, _, sources = api.stores
    assert (await sources.current()).credentials["CUBEJS_AWS_REGION"] == "us-east-1"


async def test_a_warehouse_the_portal_has_no_field_list_for_still_connects(api):
    """The free-form settings at the bottom of the form are the only way to connect something
    unlisted, so nothing here may require a set of keys it invented for it."""
    stored = await api.post(
        "/api/onboarding/connection",
        json={"driver": "timescale", "values": {"CUBEJS_DB_URL": "postgres://db/metrics"}},
    )

    assert stored.status_code == 200, stored.text


async def test_there_is_no_way_to_rotate_the_secrets(api):
    """Rotating means re-rendering both compose files and re-running both deployments, since a
    container's environment cannot be changed in place. A button that silently stops everything
    answering was worse than no button."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    assert (await api.post("/api/onboarding/deployment/secrets")).status_code == 404


async def test_there_is_no_way_to_delete_a_connection(api):
    """Removed deliberately: the edit form covers pointing somewhere else, and a delete is how
    a model and four recorded steps get orphaned in the store."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    assert (await api.delete("/api/onboarding/connection")).status_code == 405


async def test_the_catalog_is_a_503_before_a_deployment_rather_than_a_500(api, monkeypatch):
    """503 and not 500, because it is the state every install starts in — nothing is broken.

    The browser must not treat it as fatal either: for a while it did, and the page it replaced
    with "cannot reach the server" was the data panel that is the only way to configure one.
    """

    from analyst_agent.api.support import state

    async def unconfigured(_app):
        raise HTTPException(503, "no Cube deployment is configured")

    monkeypatch.setattr(state, "cube", unconfigured)
    assert (await api.get("/api/ide/model/summary")).status_code == 503


# --- the editor's two routes -------------------------------------------------
# Both are thin mappings from a `workspace` refusal to a status code, so what is asserted is the
# mapping and nothing under it — the tiers themselves are covered in test_workspace.py, and the
# Cube leg is verified against a live deployment. These two cases are the ones that never reach
# the store, which is why they need no respx.


async def test_saving_on_main_is_a_409_at_the_edge(api):
    """Main is generated: re-running the pipeline rewrites every file in it, so an edit there is
    work waiting to be discarded. Refused before any ref is created."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    await api.stores[2].patch(store_url=STORED.store_url, store_token=STORED.store_token)
    body = {"ref": "main"}
    for route in ("/api/ide/check", "/api/ide/commit", "/api/ide/discard"):
        assert (await api.post(route, json=body)).status_code == 409, route
    saved = await api.put("/api/ide/files/views/s.yml", json={"ref": "main", "text": "views: []"})
    assert saved.status_code == 409


async def test_a_path_that_is_not_a_model_file_is_a_400(api):
    """Refused where the bytes arrive, so a path Cube would ignore never reaches a draft."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    await api.stores[2].patch(store_url=STORED.store_url, store_token=STORED.store_token)
    answered = await api.put(
        "/api/ide/files/notes/escape.txt", json={"ref": "edit", "text": "views: []"}
    )
    assert answered.status_code == 400


async def test_a_change_set_with_an_unknown_field_is_refused(api):
    """`extra="forbid"` on `Draft`: a renamed field in the browser has to fail loudly rather
    than act on the default ref."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    answered = await api.post("/api/ide/commit", json={"ref": "edit", "files": {}})
    assert answered.status_code == 422


async def test_every_path_the_browser_calls_is_a_real_route():
    """The paths are the contract with `src/web/src/sources.ts`, and several of these have no
    behaviour test of their own — so a rename that misses one shows up as a 404 in the browser
    rather than here. Resolved against the routers rather than requested, so it asserts the
    surface without standing up a deployment."""
    from analyst_agent.api import onboarding
    from analyst_agent.api.ide import routes as ide

    surface = {
        (method, route.path)
        for router in (onboarding.router, onboarding.pipeline, ide.router)
        for route in router.routes
        for method in route.methods
    }
    called = {
        ("GET", "/api/onboarding/connection"),
        ("POST", "/api/onboarding/connection"),
        ("PATCH", "/api/onboarding/connection"),
        ("POST", "/api/onboarding/connection/probe"),
        ("PUT", "/api/onboarding/deployment"),
        ("GET", "/api/onboarding/deployment/{service}/compose"),
        ("GET", "/api/ide/model"),
        ("GET", "/api/ide/model/summary"),
        ("GET", "/api/ide/refs"),
        ("POST", "/api/ide/refs"),
        ("DELETE", "/api/ide/refs/{ref}"),
        ("GET", "/api/ide/refs/{ref}/diff"),
        ("POST", "/api/ide/refs/{ref}/merge"),
        ("GET", "/api/ide/files"),
        ("GET", "/api/ide/files/{path:path}"),
        ("POST", "/api/ide/check"),
        ("POST", "/api/ide/commit"),
        ("POST", "/api/ide/chats"),
        ("POST", "/api/ide/chats/{chat_id}/messages"),
        ("GET", "/api/onboarding/pipeline/tables"),
        ("POST", "/api/onboarding/pipeline/tables/closure"),
        ("POST", "/api/onboarding/pipeline/{stage}"),
        ("GET", "/api/onboarding/pipeline/job"),
        ("GET", "/api/onboarding/pipeline/job/stream"),
        ("DELETE", "/api/onboarding/pipeline/job"),
    }
    assert not called - surface, f"the browser calls routes that do not exist: {called - surface}"


async def test_a_stage_name_is_validated_rather_than_accepted(api):
    """`POST /pipeline/{stage}` is as broad as a path parameter gets, so it has to refuse
    anything that is not one of the four rather than start a job named after it."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    assert (await api.post("/api/onboarding/pipeline/nonsense", json={})).status_code == 404


# --- the routes nothing else exercises ---------------------------------------
# Written before `web/` is split into routes, controllers and DTOs (see mvc-migration.md, Phase
# 0): these payloads become pydantic response models, and a shape nothing asserts is a shape the
# split is free to change by accident. Each of these locks the keys the browser reads, not the
# machinery underneath — that has its own suite.
#
# The monkeypatches target the *route module's* attribute rather than the defining module,
# because the routes do `from ..modelstore import model_files`, which binds the name at import.
# The same trap applies to the controllers this code is about to become.


async def test_the_served_model_reports_entities_members_and_which_file_defined_each(
    api, monkeypatch, catalog
):
    """The data page's whole payload. `file` per entity is read from the store rather than
    guessed from `cubes/<name>.yml`, because a hand-edited model may put a cube anywhere."""
    from analyst_agent.api.ide import routes

    async def files(ref="main"):
        return {"cubes/everything.yml": "cubes:\n  - name: orders\n  - name: customers\n"}

    async def served(*, ref="main"):
        return catalog

    monkeypatch.setattr(routes, "model_files", files)
    monkeypatch.setattr(routes, "served_catalog", served)

    body = (await api.get("/api/ide/model")).json()
    assert (body["serving"], body["error"]) == (True, "")
    assert set(body) == {"serving", "error", "counts", "unloaded", "entities"}

    orders = next(entity for entity in body["entities"] if entity["name"] == "orders")
    assert orders["file"] == "cubes/everything.yml", "the defining file is not read off the store"
    assert orders["is_view"] is False
    assert set(orders) >= {"name", "file", "is_view", "title", "description", "members"}
    assert {member["name"] for member in orders["members"]} >= {"count", "net_revenue", "status"}
    # `name` is the member id's last segment, shipped rather than split in the browser.
    assert all(member["id"].endswith(member["name"]) for member in orders["members"])

    # The view is what carries `exposed_by`, and a view's own members carry none.
    view = next(entity for entity in body["entities"] if entity["name"] == "revenue")
    assert view["is_view"] is True
    assert all(member["exposed_by"] == [] for member in view["members"])
    # Defined in a file but never served by Cube — the signal that a file did not compile.
    assert body["unloaded"] == []


async def test_the_served_model_still_answers_when_cube_is_down(api, monkeypatch):
    """One half being unreachable must not hide the other: the files are still readable and the
    editor has to keep working, so this is a 200 saying `serving: false`."""
    from analyst_agent.api.ide import routes
    from analyst_agent.modelstore.client import StoreError

    async def files(ref="main"):
        return {"cubes/orders.yml": "cubes:\n  - name: orders\n"}

    async def down(*, ref="main"):
        raise StoreError("connection refused", where="cube")

    monkeypatch.setattr(routes, "model_files", files)
    monkeypatch.setattr(routes, "served_catalog", down)

    body = (await api.get("/api/ide/model")).json()
    assert body["serving"] is False
    assert "connection refused" in body["error"]
    assert body["entities"] == []
    assert body["unloaded"] == ["cubes/orders.yml"], "a readable file is not reported as lost"


async def test_the_ref_list_counts_changed_files_and_never_counts_main(
    api, monkeypatch, model_store, opening
):
    """Files changed and not commits: it is what a reviewer is about to read. Main has nothing to
    compare against, so it is the one ref with no number."""
    from analyst_agent.api.ide import controller

    store = model_store(main={"cubes/orders.yml": "cubes: []"}, feature={})

    async def diff(ref):
        return {"changed": ["cubes/orders.yml", "views/revenue.yml"], "patch": "diff --git…"}

    async def refs():
        return [Ref("main", "a", 1, False), Ref("feature", "b", 2, False)]

    store.diff = diff
    store.refs = refs
    monkeypatch.setattr(controller, "open_store", opening(store))

    body = (await api.get("/api/ide/refs")).json()
    assert body["main"] == "main"
    assert body["refs"] == [{"name": "main", "changed": 0}, {"name": "feature", "changed": 2}]


async def test_a_rejected_change_set_is_a_200_carrying_its_reasons(
    api, monkeypatch, model_store, opening
):
    """The editor renders errors in one banner, so a rejection is the *answer* and not a failed
    request. Splitting it across a 400 and a 200 would make one button read two code paths."""
    from analyst_agent.api.ide import controller
    from analyst_agent.modelstore.editing import workspace

    async def verify(store, ref, *, saved):
        return workspace.Verdict(
            ok=False,
            kind="rejected",
            reason="it does not compile",
            errors=["views/revenue.yml: no such member orders.nope"],
            warnings=["a tile reads this view"],
            compiled=False,
            compile_error="Member not found: orders.nope",
        )

    monkeypatch.setattr(controller, "open_store", opening(model_store(edit={})))
    monkeypatch.setattr(workspace, "verify", verify)

    answered = await api.post("/api/ide/check", json={"ref": "edit"})
    assert answered.status_code == 200, "a model Cube refuses is not a bad request"
    body = answered.json()
    assert body["ok"] is False
    assert body["compiled"] is False
    assert body["errors"] and body["warnings"]
    assert set(body) == {
        "ref",
        "ok",
        "errors",
        "warnings",
        "compiled",
        "compile_error",
        "member_errors",
        "impact",
    }
    assert body["impact"] is None, "nothing compiled, so there was no catalog to check against"


async def test_the_table_picker_reads_what_the_read_stage_cached(api):
    """Never the warehouse: this fires on every checkbox tick. `read` is false before anything has
    been read, which is what the picker shows its empty state from."""
    await api.post("/api/onboarding/connection", json=CHINOOK)
    body = (await api.get("/api/onboarding/pipeline/tables")).json()
    assert body["read"] is False
    assert body["schemas"] == []

    _, _, sources = api.stores
    await sources.save_state(
        {
            "introspection": {
                "public": {
                    "tables": [
                        {
                            "name": "invoice",
                            "schema": "public",
                            "columns": [{"name": "id", "data_type": "integer"}],
                            "primary_key": ["id"],
                            "foreign_keys": [],
                        }
                    ]
                }
            }
        }
    )
    body = (await api.get("/api/onboarding/pipeline/tables")).json()
    assert body["read"] is True
    assert [schema["schema"] for schema in body["schemas"]] == ["public"]
    assert set(body) == {"read", "truncated", "notes", "selection", "schemas"}


async def test_a_stored_result_pages_and_exports_with_labels(api):
    """The event carries only a head of the rows; this is where the rest is read from. The CSV
    header is labels and not member ids — a person reads a spreadsheet."""
    chat = (await api.post("/api/chats", json={})).json()
    columns = [
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
    rows = [
        {"orders.status": "paid", "orders.count": 3},
        {"orders.status": "open", "orders.count": 1},
    ]
    result_id = await api.stores[0].save_result(chat["id"], "turn-1", columns, rows)

    body = (await api.get(f"/api/results/{result_id}?offset=1&limit=1")).json()
    assert (body["row_count"], body["offset"]) == (2, 1)
    assert body["rows"] == [rows[1]], "the page is not sliced by offset and limit"

    exported = await api.get(f"/api/results/{result_id}/csv")
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.text.splitlines()[0] == "Status,Orders"

    assert (await api.get("/api/results/nope")).status_code == 404


async def test_agent_access_reports_the_token_and_a_config_for_the_address_it_was_asked_on(api):
    """`base_url` and not `settings.web_host`: reached over a tunnel, the settings pair still says
    127.0.0.1, which is a config that cannot work from wherever the person actually is."""
    from analyst_agent.core.access import Access

    server.app.state.mcp = Access(enabled=True, token="tok-abc")
    server.app.state.mcp_max_rows = 100

    body = (await api.get("/api/mcp")).json()
    assert body["enabled"] is True
    assert body["token"] == "tok-abc"
    assert body["url"] == "http://test/mcp"
    assert "tok-abc" in body["desktop"] and "tok-abc" in body["cli"]
    assert {tool["name"] for tool in body["tools"]} == {
        "search_model",
        "run_query",
        "describe_model",
    }

    off = await api.post("/api/mcp/enabled", json={"enabled": False})
    assert off.json() == {"enabled": False}
    assert server.app.state.mcp.enabled is False

    rotated = (await api.post("/api/mcp/rotate")).json()
    assert rotated["token"] != "tok-abc"
    assert rotated["token"] in rotated["desktop"], "a new token beside a stale config block"
