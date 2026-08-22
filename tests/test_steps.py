"""`steps.action` — the one dispatcher every caller of a step goes through.

No containers and no model calls: the steps are stubbed. What is under test is the behaviour
that belongs here rather than in each caller, and would drift if it were duplicated — the
order rule, and what a failure leaves in the database.
"""

import pytest

from analyst_agent.core import db
from analyst_agent.core import deployment as _deployment
from analyst_agent.core.deployment import Deployment
from analyst_agent.datamodel.modelgen import steps
from analyst_agent.datamodel.modelgen.steps import StepFailed

SHOP = Deployment(store_url="http://localhost:8500", driver="postgres", credentials={"x": "1"})


@pytest.fixture
async def store():
    await db.create_tables()
    s = _deployment
    await s.save(SHOP)
    return s


@pytest.fixture(autouse=True)
def no_store(monkeypatch):
    """The model comes from the store over HTTP, and these tests are about the dispatcher."""

    async def empty(ref="main"):
        return {}

    monkeypatch.setattr(steps, "model_files", empty)


async def test_an_unknown_action_is_refused():
    with pytest.raises(StepFailed, match="unknown action"):
        await steps.action("enrich")


async def test_a_step_cannot_run_before_the_one_it_depends_on(store):
    """Enforced here, not in the browser — the browser is advice, not a guarantee."""
    with pytest.raises(StepFailed, match="run cubes first"):
        await steps.action("describe")
    with pytest.raises(StepFailed, match="run describe first"):
        await steps.action("views")


async def test_read_is_reachable_without_any_step_and_records_none(store, monkeypatch):
    """`read` produces the catalog the picker needs, so it is not one of the four rows."""

    async def fake_read(st, report=None):
        return {"schemas": [{"schema": "public", "tables": 3}]}

    monkeypatch.setattr(steps, "read_catalog", fake_read)
    result = await steps.action("read")
    assert result.summary["schemas"][0]["tables"] == 3
    assert await store.steps() == {}, "read has no row in the panel"


async def test_a_failing_step_is_recorded_before_it_is_re_raised(store, monkeypatch):
    """Recorded here and not by the route, so a failure outside the portal records it too."""

    async def boom(st, report=None):
        raise StepFailed("Cube rejected the model")

    monkeypatch.setattr(steps, "cubes", lambda *a, **k: boom(a[0]))

    with pytest.raises(StepFailed, match="Cube rejected"):
        await steps.action("cubes")

    record = (await store.steps())["cubes"]
    assert record["status"] == "error"
    assert record["error"] == "Cube rejected the model"
    assert record["finished_at"] > 0


async def test_a_failing_read_records_nothing(store, monkeypatch):
    """It has no step row to mark, and inventing one would break `save_step`."""

    async def boom(st, report=None):
        raise StepFailed("no schemas")

    monkeypatch.setattr(steps, "read_catalog", boom)
    with pytest.raises(StepFailed):
        await steps.action("read")
    assert await store.steps() == {}


async def test_a_stage_with_no_branch_is_refused_rather_than_run_as_views(store, monkeypatch):
    """`STAGES` is what the membership check accepts, so a name added there with no branch in
    the dispatch has to be refused rather than fall through to whichever step is last."""
    monkeypatch.setattr(steps, "STAGES", (*steps.STAGES, "publish"))
    with pytest.raises(StepFailed, match="stage with no branch"):
        await steps.action("publish")


def test_a_rejected_model_is_named_where_it_can_still_be_read():
    """`install` leaves a model Cube refused on the candidate ref and the served model alone, so
    the failure has somewhere to point. Quoting the compile error alone would send someone
    looking for YAML that is not in the model being served."""
    refused = steps.publish.Validation(compile_error="Unexpected YAML key", candidate="candidate")
    message = steps._rejected("the model", refused)
    assert "Unexpected YAML key" in message and "'candidate'" in message
    assert "has not changed" in message
    # Nothing to point at when the store never got as far as a candidate.
    assert "candidate" not in steps._rejected("the model", steps.publish.Validation())


async def test_generate_runs_the_actions_in_order(store, monkeypatch):
    order: list[str] = []

    def stub(name):
        async def call(*args, **kwargs):
            order.append(name)
            return {} if name == "read" else steps.StepResult()

        return call

    for name in ("cubes", "describe", "views"):
        monkeypatch.setattr(steps, name, stub(name))
    monkeypatch.setattr(steps, "read_catalog", stub("read"))
    # The order rule reads the state each step needs, and the stubs never write it.
    monkeypatch.setattr(store, "state", _built)

    await steps.generate(enrich=True)
    assert order == ["read", "cubes", "describe", "views"]


async def test_generate_stops_before_the_llm_steps_without_enrich(store, monkeypatch):
    order: list[str] = []

    def stub(name):
        async def call(*args, **kwargs):
            order.append(name)
            return {} if name == "read" else steps.StepResult()

        return call

    for name in ("cubes", "describe", "views"):
        monkeypatch.setattr(steps, name, stub(name))
    monkeypatch.setattr(steps, "read_catalog", stub("read"))
    monkeypatch.setattr(store, "state", _built)

    await steps.generate()
    assert order == ["read", "cubes"]


async def _built() -> dict:
    """What the earlier steps leave behind — `blocked` reads this, not a recorded tick."""
    return {"catalog": {"public": {}}, "bodies": {"a": {}}, "enrichment": {}}
