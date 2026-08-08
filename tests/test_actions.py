"""`run.action` — the one dispatcher the CLI and the portal share.

No containers and no model calls: the steps are stubbed. What is under test is the
behaviour that used to live in each caller and could therefore drift — the order rule,
and what a failure leaves in the database.
"""

import pytest

from analyst_agent.sources.model import run
from analyst_agent.sources.model.run import StepFailed
from analyst_agent.sources.registry import Source
from analyst_agent.sources.store import SourceStore

SHOP = Source(name="shop", description="", driver="postgres", port=4001, credentials={"x": "1"})


@pytest.fixture
async def store(tmp_path) -> SourceStore:
    s = SourceStore(tmp_path / "sources.db")
    await s.init()
    await s.add(SHOP)
    return s


async def test_an_unknown_action_is_refused():
    with pytest.raises(StepFailed, match="unknown action"):
        await run.action("enrich", SHOP, None)


async def test_a_step_cannot_run_before_the_one_it_depends_on(store):
    """Enforced here, not in the browser — the browser is advice, not a guarantee."""
    with pytest.raises(StepFailed, match="run connect first"):
        await run.action("cubes", SHOP, store)
    with pytest.raises(StepFailed, match="run cubes first"):
        await store.save_step("shop", "connect", {"status": "done"})
        await run.action("describe", SHOP, store)


async def test_connect_is_always_reachable(store, monkeypatch):
    """It is the first step; nothing precedes it."""
    called = []

    async def fake_connect(source, st, report=None):
        called.append(source.name)
        return run.StepResult(summary={"ok": 1})

    monkeypatch.setattr(run, "connect", fake_connect)
    result = await run.action("connect", SHOP, store)
    assert called == ["shop"]
    assert result.summary == {"ok": 1}


async def test_read_is_reachable_without_any_step_and_records_none(store, monkeypatch):
    """`read` produces the catalog the picker needs, so it is not one of the four rows."""

    async def fake_read(source, st, report=None):
        return {"schemas": [{"schema": "public", "tables": 3}]}

    monkeypatch.setattr(run, "read_catalog", fake_read)
    result = await run.action("read", SHOP, store)
    assert result.summary["schemas"][0]["tables"] == 3
    assert await store.steps("shop") == {}, "read has no row in the panel"


async def test_a_failing_step_is_recorded_before_it_is_re_raised(store, monkeypatch):
    """The route used to write this itself, so a CLI failure recorded nothing at all."""

    async def boom(source, st, report=None):
        raise StepFailed("Cube rejected the model")

    await store.save_step("shop", "connect", {"status": "done"})
    monkeypatch.setattr(run, "cubes", lambda *a, **k: boom(*a[:2]))

    with pytest.raises(StepFailed, match="Cube rejected"):
        await run.action("cubes", SHOP, store)

    record = (await store.steps("shop"))["cubes"]
    assert record["status"] == "error"
    assert record["error"] == "Cube rejected the model"
    assert record["finished_at"] > 0


async def test_a_failing_read_records_nothing(store, monkeypatch):
    """It has no step row to mark, and inventing one would break `save_step`."""

    async def boom(source, st, report=None):
        raise StepFailed("no schemas")

    monkeypatch.setattr(run, "read_catalog", boom)
    with pytest.raises(StepFailed):
        await run.action("read", SHOP, store)
    assert await store.steps("shop") == {}


async def test_generate_runs_the_actions_in_order(store, monkeypatch):
    order: list[str] = []

    def stub(name):
        async def call(*args, **kwargs):
            order.append(name)
            return {} if name == "read" else run.StepResult()

        return call

    for name in ("connect", "cubes", "describe", "views"):
        monkeypatch.setattr(run, name, stub(name))
    monkeypatch.setattr(run, "read_catalog", stub("read"))
    # The order rule reads the store, and the stubs never write it.
    monkeypatch.setattr(store, "steps", lambda name: _done())

    await run.generate(SHOP, store, enrich=True)
    assert order == ["connect", "read", "cubes", "describe", "views"]


async def test_generate_stops_before_the_llm_steps_without_enrich(store, monkeypatch):
    order: list[str] = []

    def stub(name):
        async def call(*args, **kwargs):
            order.append(name)
            return {} if name == "read" else run.StepResult()

        return call

    for name in ("connect", "cubes", "describe", "views"):
        monkeypatch.setattr(run, name, stub(name))
    monkeypatch.setattr(run, "read_catalog", stub("read"))
    monkeypatch.setattr(store, "steps", lambda name: _done())

    await run.generate(SHOP, store)
    assert order == ["connect", "read", "cubes"]


async def _done() -> dict:
    return {step: {"status": "done"} for step in ("connect", "cubes", "describe", "views")}
