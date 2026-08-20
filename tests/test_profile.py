"""What the agent is told about a file it was given.

The profiler runs in the sandbox service and is checked by that service's own suite. What is here
is the portal's half: that what reaches the prompt is the shape rather than the file, and that a
sandbox which is absent or silent costs a turn nothing.
"""

import httpx
import pytest
import respx

from analyst_agent.core.deployment import Deployment
from analyst_agent.sandbox import profile

URL = "http://sandbox:8600"


@pytest.fixture
def configured(monkeypatch):
    async def deployment():
        return Deployment(sandbox_url=URL, sandbox_token="tok")

    monkeypatch.setattr("analyst_agent.sandbox.client.current", deployment)


def a_profile(**kwargs) -> profile.Profile:
    return profile.Profile(
        path="uploads/orders.csv",
        bytes=2048,
        rows=1000,
        columns=[
            profile.ColumnProfile(
                name="order_id", type="BIGINT", distinct=1000, min="1", max="1000", unique=True
            ),
            profile.ColumnProfile(
                name="customer", type="VARCHAR", distinct=12, nulls=0.02, min="ACME", max="Zinc"
            ),
        ],
        head=[{"order_id": "1", "customer": "ACME"}],
        **kwargs,
    )


def test_the_prompt_carries_the_shape_and_not_the_file():
    """A 200 MB CSV cannot go into a context. The summary is what makes an upload usable without
    a `run_python` to discover the columns first."""
    said = a_profile().as_prompt()
    assert "uploads/orders.csv" in said
    assert "1,000 rows × 2 columns" in said
    assert "order_id (BIGINT) — 1,000 distinct, unique" in said
    assert "2% null" in said
    # The head is a sample, and a sample is three rows rather than the file.
    assert said.count("ACME") == 2


def test_a_file_with_no_readable_shape_is_still_named():
    said = profile.Profile(
        path="uploads/notes.pdf", bytes=12, note="IOError: not a table"
    ).as_prompt()
    assert "uploads/notes.pdf" in said
    assert "IOError" in said


@respx.mock
async def test_the_shape_read_on_arrival_is_what_reaches_the_prompt(configured):
    """One request per turn, not one profiling run per file: this is in the system prompt for the
    life of the chat."""
    route = respx.get(f"{URL}/profiles/c1").mock(
        return_value=httpx.Response(
            200,
            json={
                "profiles": [{"path": "uploads/orders.csv", "bytes": 12, "rows": 3, "columns": []}]
            },
        )
    )
    saved = await profile.saved("c1")
    assert [p.path for p in saved] == ["uploads/orders.csv"]
    assert route.call_count == 1


@respx.mock
async def test_an_upload_the_service_could_not_read_is_still_offered_to_the_agent(configured):
    """The listing is the authority and the shape is only the shape. A file the profiler failed
    on is one the agent can still open."""
    respx.get(f"{URL}/profiles/c1").mock(
        return_value=httpx.Response(
            200, json={"profiles": [{"path": "uploads/notes.pdf", "note": "not profiled"}]}
        )
    )
    saved = await profile.saved("c1")
    assert saved[0].note == "not profiled"


@respx.mock
async def test_a_sandbox_that_is_not_answering_costs_the_turn_nothing(configured):
    """A file's shape is worth having and not worth failing a turn over — the agent can still
    query, and it just does not know what it was handed."""
    respx.get(f"{URL}/profiles/c1").mock(side_effect=httpx.ConnectError("x"))
    assert await profile.saved("c1") == []


async def test_no_sandbox_configured_is_the_same_answer(monkeypatch):
    """Every deployment until one is run. A raise here would take out every chat turn."""

    async def none():
        return Deployment()

    monkeypatch.setattr("analyst_agent.sandbox.client.current", none)
    assert await profile.saved("c1") == []
