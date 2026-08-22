from dataclasses import dataclass, field

import pytest

from analyst_agent.core.config import settings
from analyst_agent.core.deployment import MAIN, Deployment
from analyst_agent.cube.catalog import Catalog
from analyst_agent.cube.client import CompileResult, CubeError, LoadResult
from analyst_agent.cube.schema import CubeQuery
from analyst_agent.modelstore.client import StoreError


@pytest.fixture(autouse=True)
def scratch_data_dir(monkeypatch, tmp_path_factory):
    """Nothing may reach the developer's own ~/.analyst. The deployment row lives in
    analyst.db and `open_client` reads it, so an unpinned data dir would point a test at a
    real Cube."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path_factory.mktemp("data")))


@pytest.fixture
def deployment() -> Deployment:
    return Deployment(
        store_url="http://localhost:8500",
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
    """The second adapter at the Cube seam — a `CubeClient` with no HTTP. Anything taking a
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
            raise CubeError(self.down)

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
            raise CubeError(self.fail_load)
        return LoadResult(
            rows=list(self.rows), duration_ms=7, resolved_ranges=dict(self.resolved_ranges)
        )


@pytest.fixture
def cube(catalog) -> FakeCube:
    return FakeCube(served=catalog)


class FakeStore:
    """Refs as dicts of path -> text. `edit` is the only mutation, as it is on the real one."""

    def __init__(self, **refs: dict[str, str]):
        self.refs = {name: dict(files) for name, files in refs.items()}
        self.commits: list[str] = []
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

    async def edit(self, ref, write=None, delete=None, message="edit model") -> str:
        if self.fail:
            raise StoreError(self.fail)
        self.refs[ref].update(write or {})
        for path in delete or []:
            self.refs[ref].pop(path, None)
        self.commits.append(message)
        return "sha"


@pytest.fixture
def model_store():
    """The FakeStore class, so a test can build the refs it needs without importing across
    test modules — `tests/` is not a package."""
    return FakeStore
