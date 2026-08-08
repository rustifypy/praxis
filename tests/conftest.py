import pytest

from analyst_agent.cube.catalog import Catalog

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
