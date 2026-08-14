"""`steps.action` — the one dispatcher every caller of a step goes through.

No containers and no model calls: the steps are stubbed. What is under test is the behaviour
that belongs here rather than in each caller, and would drift if it were duplicated — the
order rule, and what a failure leaves in the database.
"""

import pytest

from analyst_agent.sources import vcs
from analyst_agent.sources.modelgen import steps
from analyst_agent.sources.modelgen.steps import StepFailed
from analyst_agent.sources.registry import Source
from analyst_agent.sources.store import SourceStore

SHOP = Source(name="shop", description="", driver="postgres", port=4001, credentials={"x": "1"})


@pytest.fixture
async def store(tmp_path) -> SourceStore:
    s = SourceStore(tmp_path / "analyst.db")
    await s.init()
    await s.add(SHOP)
    return s


async def test_an_unknown_action_is_refused():
    with pytest.raises(StepFailed, match="unknown action"):
        await steps.action("enrich", SHOP, None)


async def test_a_step_cannot_run_before_the_one_it_depends_on(store):
    """Enforced here, not in the browser — the browser is advice, not a guarantee."""
    with pytest.raises(StepFailed, match="run connect first"):
        await steps.action("cubes", SHOP, store)
    with pytest.raises(StepFailed, match="run cubes first"):
        await store.save_step("shop", "connect", {"status": "done"})
        await steps.action("describe", SHOP, store)


async def test_connect_is_always_reachable(store, monkeypatch):
    """It is the first step; nothing precedes it."""
    called = []

    async def fake_connect(source, st, report=None):
        called.append(source.name)
        return steps.StepResult(summary={"ok": 1})

    monkeypatch.setattr(steps, "connect", fake_connect)
    result = await steps.action("connect", SHOP, store)
    assert called == ["shop"]
    assert result.summary == {"ok": 1}


async def test_read_is_reachable_without_any_step_and_records_none(store, monkeypatch):
    """`read` produces the catalog the picker needs, so it is not one of the four rows."""

    async def fake_read(source, st, report=None):
        return {"schemas": [{"schema": "public", "tables": 3}]}

    monkeypatch.setattr(steps, "read_catalog", fake_read)
    result = await steps.action("read", SHOP, store)
    assert result.summary["schemas"][0]["tables"] == 3
    assert await store.steps("shop") == {}, "read has no row in the panel"


async def test_a_failing_step_is_recorded_before_it_is_re_raised(store, monkeypatch):
    """Recorded here and not by the route, so a failure outside the portal records it too."""

    async def boom(source, st, report=None):
        raise StepFailed("Cube rejected the model")

    await store.save_step("shop", "connect", {"status": "done"})
    monkeypatch.setattr(steps, "cubes", lambda *a, **k: boom(*a[:2]))

    with pytest.raises(StepFailed, match="Cube rejected"):
        await steps.action("cubes", SHOP, store)

    record = (await store.steps("shop"))["cubes"]
    assert record["status"] == "error"
    assert record["error"] == "Cube rejected the model"
    assert record["finished_at"] > 0


async def test_a_failing_read_records_nothing(store, monkeypatch):
    """It has no step row to mark, and inventing one would break `save_step`."""

    async def boom(source, st, report=None):
        raise StepFailed("no schemas")

    monkeypatch.setattr(steps, "read_catalog", boom)
    with pytest.raises(StepFailed):
        await steps.action("read", SHOP, store)
    assert await store.steps("shop") == {}


async def test_a_stage_with_no_branch_is_refused_rather_than_run_as_views(store, monkeypatch):
    """`STAGES` is what the membership check accepts, so a name added there with no branch in
    the dispatch has to be refused rather than fall through to whichever step is last."""
    monkeypatch.setattr(steps, "STAGES", (*steps.STAGES, "publish"))
    await store.save_step("shop", "connect", {"status": "done"})
    with pytest.raises(StepFailed, match="stage with no branch"):
        await steps.action("publish", SHOP, store)


async def test_only_the_model_writing_actions_commit(store, monkeypatch):
    """Both the success and the failure path test `WRITES_MODEL` explicitly, rather than relying
    on where `connect` and `read` leave the dispatch: a commit for a catalog read would put an
    empty commit in the model repo under a name that claims it rewrote the model."""
    committed: list[str] = []

    async def fake_commit(path, message):
        committed.append(message)
        return vcs.Result(code=0, out="", err="")

    async def nothing(path):
        return vcs.Result(code=0, out="", err="")

    async def ok(*args, **kwargs):
        return steps.StepResult(summary={"cubes": 3})

    async def catalog(*args, **kwargs):
        return {"schemas": []}

    async def boom(*args, **kwargs):
        raise StepFailed("no schemas")

    # Stubbed rather than mocked-out: `model_dir()` only computes a path, so with these two
    # replaced nothing in this test touches git or the working directory.
    monkeypatch.setattr(steps.vcs, "commit", fake_commit)
    monkeypatch.setattr(steps.vcs, "ensure", nothing)
    monkeypatch.setattr(steps, "connect", ok)
    monkeypatch.setattr(steps, "read_catalog", catalog)
    monkeypatch.setattr(steps, "cubes", ok)

    await steps.action("connect", SHOP, store)
    await steps.action("read", SHOP, store)
    assert committed == [], "starting a container and caching a catalog write no model files"

    monkeypatch.setattr(steps, "read_catalog", boom)
    with pytest.raises(StepFailed):
        await steps.action("read", SHOP, store)
    assert committed == [], "and a failed read has nothing to commit either"

    await store.save_step("shop", "connect", {"status": "done"})
    await steps.action("cubes", SHOP, store)
    assert committed == ["cubes — cubes=3"]


async def test_generate_runs_the_actions_in_order(store, monkeypatch):
    order: list[str] = []

    def stub(name):
        async def call(*args, **kwargs):
            order.append(name)
            return {} if name == "read" else steps.StepResult()

        return call

    for name in ("connect", "cubes", "describe", "views"):
        monkeypatch.setattr(steps, name, stub(name))
    monkeypatch.setattr(steps, "read_catalog", stub("read"))
    # The order rule reads the store, and the stubs never write it.
    monkeypatch.setattr(store, "steps", lambda name: _done())

    await steps.generate(SHOP, store, enrich=True)
    assert order == ["connect", "read", "cubes", "describe", "views"]


async def test_generate_stops_before_the_llm_steps_without_enrich(store, monkeypatch):
    order: list[str] = []

    def stub(name):
        async def call(*args, **kwargs):
            order.append(name)
            return {} if name == "read" else steps.StepResult()

        return call

    for name in ("connect", "cubes", "describe", "views"):
        monkeypatch.setattr(steps, name, stub(name))
    monkeypatch.setattr(steps, "read_catalog", stub("read"))
    monkeypatch.setattr(store, "steps", lambda name: _done())

    await steps.generate(SHOP, store)
    assert order == ["connect", "read", "cubes"]


async def _done() -> dict:
    return {step: {"status": "done"} for step in ("connect", "cubes", "describe", "views")}
