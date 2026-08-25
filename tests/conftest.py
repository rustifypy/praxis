from dataclasses import dataclass, field

import httpx
import pytest

from analyst_agent.core.config import settings
from analyst_agent.core.deployment import MAIN, Deployment
from analyst_agent.modelstore.catalog import Catalog
from analyst_agent.modelstore.client import CompileResult, LoadResult, StoreError
from analyst_agent.modelstore.schema import CubeQuery


@pytest.fixture(autouse=True)
def scratch_data_dir(monkeypatch, tmp_path_factory):
    """Nothing may reach the developer's own ~/.analyst. The deployment row lives in
    analyst.db and `open_client` reads it, so an unpinned data dir would point a test at a
    real Cube."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path_factory.mktemp("data")))


@pytest.fixture
def deployment() -> Deployment:
    return Deployment(
        store_url="http://localhost:8000",
        store_token="w" * 40,
        api_secret="s" * 40,
        driver="postgres",
        credentials={"CUBEJS_DB_NAME": "shop"},
    )


META = {
    "cubes": [
        {
            "name": "orders",
            "type": "cube",
            "measures": [
                {"name": "orders.count", "type": "count"},
                {
                    "name": "orders.net_revenue",
                    "type": "sum",
                    "meta": {"ai_context": "default revenue measure"},
                },
            ],
            "dimensions": [
                {"name": "orders.status", "type": "string"},
                {
                    "name": "orders.created_at",
                    "type": "time",
                    "granularities": [{"name": "month"}, {"name": "day"}],
                },
            ],
            "segments": [{"name": "orders.paid_only"}],
        },
        {
            "name": "customers",
            "type": "cube",
            "measures": [{"name": "customers.count", "type": "count"}],
            "dimensions": [{"name": "customers.country", "type": "string"}],
        },
        {
            "name": "revenue",
            "type": "view",
            "description": "Revenue and order volume.",
            # Cube emits `aliasMember` on every view member — it is the only thing in
            # /v1/meta that says which cube member a view re-exports.
            "measures": [
                {"name": "revenue.count", "type": "count", "aliasMember": "orders.count"},
                {
                    "name": "revenue.net_revenue",
                    "type": "sum",
                    "aliasMember": "orders.net_revenue",
                },
            ],
            "dimensions": [
                {
                    "name": "revenue.status",
                    "type": "string",
                    "aliasMember": "orders.status",
                },
                {
                    "name": "revenue.created_at",
                    "type": "time",
                    "granularities": [{"name": "month"}],
                    "aliasMember": "orders.created_at",
                },
            ],
        },
    ]
}


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.from_meta(META)


@dataclass
class FakeCube:
    """The second adapter at the Cube seam — a `Bound` with no HTTP. Anything taking a
    session (`gate`, `Session`, `dashboards.refresh`, `EditContext`) takes one in a test."""

    served: Catalog
    root: str = "http://localhost:4000/cubejs-api"
    """Part of the seam now: the catalog route reports which address the session is on."""
    sql: str = "SELECT 1"
    rows: list[dict] = field(default_factory=list)
    resolved_ranges: dict[str, list[str]] = field(default_factory=dict)
    refuse: str = ""
    """Non-empty: compile fails the way Cube refusing a member does."""
    fail_load: str = ""
    """Non-empty: compile succeeds and execution breaks — the `failed` half of the split."""
    down: str = ""
    """Non-empty: every call raises, the way a stopped container does."""
    compiled: list[dict] = field(default_factory=list)
    loaded: list[dict] = field(default_factory=list)
    """Kept apart so "every tile compiled before any executed" is an assertion."""

    def _answering(self) -> None:
        if self.down:
            raise StoreError(self.down, where="cube")

    async def catalog(self, *, refresh: bool = False) -> Catalog:
        self._answering()
        return self.served

    async def compile(self, query: CubeQuery) -> CompileResult:
        self._answering()
        self.compiled.append(query.to_payload())
        if self.refuse:
            return CompileResult(ok=False, error=self.refuse)
        return CompileResult(ok=True, sql=self.sql, params=[])

    async def load(self, query: CubeQuery) -> LoadResult:
        self._answering()
        self.loaded.append(query.to_payload())
        if self.fail_load:
            raise StoreError(self.fail_load, where="cube")
        return LoadResult(
            rows=list(self.rows), duration_ms=7, resolved_ranges=dict(self.resolved_ranges)
        )


@pytest.fixture
def cube(catalog) -> FakeCube:
    return FakeCube(served=catalog)


class FakeStore:
    """Refs as dicts of path -> text. `edit` is the only mutation, as it is on the real one.

    `create_ref` and `delete_ref` are here for `workspace.verify`, which stands up a throwaway
    ref to compile a change set on. `made` and `dropped` record them, so a test can assert that a
    verify cleaned up after itself without inspecting `refs` for absence.
    """

    def __init__(self, **refs: dict[str, str]):
        self.refs = {name: dict(files) for name, files in refs.items()}
        self.revs = dict.fromkeys(refs, 1)
        self.commits: list[str] = []
        self.made: list[tuple[str, str]] = []
        self.dropped: list[str] = []
        self.fail = ""

    async def files(self, ref: str = MAIN, *, refresh: bool = False) -> dict[str, str]:
        if self.fail:
            raise StoreError(self.fail)
        return dict(self.refs.get(ref) or {})

    async def file(self, path: str, ref: str = MAIN) -> str:
        files = self.refs.get(ref) or {}
        if path not in files:
            raise StoreError("no such file", status=404)
        return files[path]

    async def rev(self, ref: str = MAIN) -> int:
        if self.fail:
            raise StoreError(self.fail)
        if ref not in self.refs:
            raise StoreError(f"unknown ref: {ref}", status=404)
        return self.revs.get(ref, 1)

    async def edit(self, ref, write=None, delete=None, message="edit model") -> int:
        if self.fail:
            raise StoreError(self.fail)
        self.refs[ref].update(write or {})
        for path in delete or []:
            self.refs[ref].pop(path, None)
        self.commits.append(message)
        self.revs[ref] = self.revs.get(ref, 1) + 1
        return self.revs[ref]

    async def replace(self, ref, files: dict[str, str], message: str) -> int:
        self.refs[ref] = dict(files)
        self.commits.append(message)
        self.revs[ref] = self.revs.get(ref, 1) + 1
        return self.revs[ref]

    async def merge(self, from_ref: str, into: str = MAIN) -> int:
        if self.fail:
            raise StoreError(self.fail)
        self.refs.setdefault(into, {}).update(self.refs.get(from_ref) or {})
        self.revs[into] = self.revs.get(into, 1) + 1
        return self.revs[into]

    async def create_ref(self, name: str, from_ref: str = MAIN) -> str:
        if self.fail:
            raise StoreError(self.fail)
        self.refs[name] = dict(self.refs.get(from_ref) or {})
        self.revs[name] = 1
        self.made.append((name, from_ref))
        return "sha"

    async def delete_ref(self, name: str) -> None:
        if self.fail:
            raise StoreError(self.fail)
        if name not in self.refs:
            # 404 is what the real store answers, and `verify` relies on `missing` to tell
            # "there was nothing to clear" from "it would not go".
            raise StoreError(f"no such ref: {name!r}", status=404)
        del self.refs[name]
        self.dropped.append(name)


@pytest.fixture
def model_store():
    """The FakeStore class, so a test can build the refs it needs without importing across
    test modules — `tests/` is not a package."""
    return FakeStore


@pytest.fixture
async def api(tmp_path, monkeypatch, cube):
    """The whole app, with its state wired to a tmp database and a fake Cube.

    Here rather than in one test module because two of them drive the HTTP surface now:
    `test_http` asserts behaviour and `test_payloads` snapshots what comes back.

    `app.state` is populated directly rather than by running the lifespan, because the real one
    opens `~/.analyst` — everything here is under tmp_path.
    """
    from analyst_agent.core import db, jobs, transcript
    from analyst_agent.core import deployment as connection
    from analyst_agent.dashboards import store as boards
    from analyst_agent.web import server
    from analyst_agent.web.support import state

    # The stores read `settings.db_path`, so pointing the setting is the whole of what keeps
    # these routes off the real ~/.analyst.
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    await db.create_tables()

    server.app.state.jobs = jobs.Runner()
    server.app.state.health = None
    server.app.state.cube = cube
    server.app.state.cube_key = None
    # The routes ask for the session through `state.cube`, which rebuilds it when the row's
    # address moves. Patching the accessor is what hands them the fake instead — and it only
    # works because the routes call `state.cube(app)` through the module rather than binding the
    # function at import. Anything that moves this code has to keep doing that.
    monkeypatch.setattr(state, "cube", handing_over(cube))
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
        client.stores = (transcript, boards, connection)  # type: ignore[attr-defined]
        yield client


def handing_over(cube):
    """The `state.cube` accessor, handing over a fake. Module-level because the `api` fixture
    itself needs it; a test that wants it asks for the `opening` fixture's sibling below."""

    async def accessor(_app):
        return cube

    return accessor


@pytest.fixture
def opening():
    """A stand-in for `open_store`, which is awaited *and then* entered — `async with await
    open_store() as store`. Both halves have to be faked or the route gets a coroutine where it
    expects a client.

    A fixture rather than an importable function because `tests/` is not a package, which is the
    same reason `model_store` hands over a class."""

    def make(store):
        class Opened:
            async def __aenter__(self):
                return store

            async def __aexit__(self, *exc):
                return None

        async def opener():
            return Opened()

        return opener

    return make
