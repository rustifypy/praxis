from dataclasses import dataclass, field

import httpx
import pytest

from analyst_agent.cube.catalog import Catalog
from analyst_agent.cube.client import CompileResult, CubeError, LoadResult
from analyst_agent.cube.schema import CubeQuery

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
    """The second adapter at the Cube seam — `client.Cube` with no HTTP. Anything taking a
    session (`gate`, `Session`, `dashboards.run`, `EditContext`) takes one of these in a test."""

    served: Catalog
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
            raise CubeError(self.down, cause=httpx.ConnectError(self.down))

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
            raise CubeError(self.fail_load, status=400)
        return LoadResult(
            rows=list(self.rows), duration_ms=7, resolved_ranges=dict(self.resolved_ranges)
        )


@pytest.fixture
def cube(catalog) -> FakeCube:
    return FakeCube(served=catalog)
