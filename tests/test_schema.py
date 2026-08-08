import pytest
from pydantic import ValidationError

from analyst_agent.cube.schema import CubeQuery


def test_minimal_query():
    q = CubeQuery(measures=["orders.count"])
    assert q.to_payload() == {"measures": ["orders.count"], "offset": 0}


def test_absent_limit_stays_absent():
    """An unset limit must not look like a requested one: `gate.semantic_check`
    warns about a limit without an order, and a default here fired it on every query.
    `gate.run` applies max_rows once the warning has been decided."""
    assert CubeQuery(measures=["orders.count"]).limit is None


def test_empty_query_rejected():
    with pytest.raises(ValidationError, match="at least one measure or dimension"):
        CubeQuery()


def test_ungrouped_not_accepted():
    with pytest.raises(ValidationError):
        CubeQuery.model_validate({"measures": ["orders.count"], "ungrouped": True})


def test_set_operator_takes_no_values():
    with pytest.raises(ValidationError, match="takes no values"):
        CubeQuery.model_validate(
            {
                "measures": ["orders.count"],
                "filters": [{"member": "orders.status", "operator": "set", "values": ["x"]}],
            }
        )


def test_operator_requires_values():
    with pytest.raises(ValidationError, match="requires values"):
        CubeQuery.model_validate(
            {
                "measures": ["orders.count"],
                "filters": [{"member": "orders.status", "operator": "equals"}],
            }
        )


def test_limit_capped():
    with pytest.raises(ValidationError):
        CubeQuery(measures=["orders.count"], limit=999_999)


def test_nested_filters_parse_and_flatten():
    q = CubeQuery.model_validate(
        {
            "measures": ["orders.count"],
            "filters": [
                {
                    "or": [
                        {"member": "orders.status", "operator": "equals", "values": ["paid"]},
                        {
                            "and": [
                                {
                                    "member": "orders.status",
                                    "operator": "equals",
                                    "values": ["shipped"],
                                },
                            ]
                        },
                    ]
                }
            ],
        }
    )
    assert len(list(q.iter_filters())) == 2


def test_filter_group_needs_one_operator():
    with pytest.raises(ValidationError, match="exactly one"):
        CubeQuery.model_validate({"measures": ["orders.count"], "filters": [{}]})


def test_referenced_members_covers_every_slot():
    q = CubeQuery.model_validate(
        {
            "measures": ["orders.count"],
            "dimensions": ["orders.status"],
            "segments": ["orders.paid_only"],
            "timeDimensions": [{"dimension": "orders.created_at", "granularity": "month"}],
            "filters": [{"member": "customers.country", "operator": "equals", "values": ["IN"]}],
            "order": {"orders.count": "desc"},
        }
    )
    assert set(q.referenced_members()) == {
        "orders.count",
        "orders.status",
        "orders.paid_only",
        "orders.created_at",
        "customers.country",
    }
