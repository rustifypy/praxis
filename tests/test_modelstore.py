"""The portal's client for the model store. No store, no containers — respx answers.

The store takes one file per request and commits separately; this is where that is hidden.
"""

import httpx
import pytest
import respx

from analyst_agent.core.deployment import Deployment
from analyst_agent.modelstore import ModelStore, StoreError, open_store

URL = "http://store:8000"


@pytest.fixture
async def store():
    async with ModelStore(URL, "servicetok") as client:
        yield client


def ref_route(rev: int, ref: str = "main", sha: str = "abc"):
    return respx.get(f"{URL}/refs/{ref}").mock(
        return_value=httpx.Response(200, json={"ref": ref, "sha": sha, "rev": rev, "dirty": False})
    )


def model_routes(rev: int, files: dict, ref: str = "main"):
    """The tree, plus one route per file — which is what a read of the whole model costs."""
    listed = [{"path": path, "size": len(text), "modified": 0.0} for path, text in files.items()]
    tree = respx.get(f"{URL}/refs/{ref}/tree").mock(
        return_value=httpx.Response(200, json={"ref": ref, "rev": rev, "files": listed})
    )
    for path, text in files.items():
        respx.get(f"{URL}/refs/{ref}/files/{path}").mock(
            return_value=httpx.Response(200, text=text)
        )
    return tree


def write_routes(ref: str, rev: int = 2):
    return (
        respx.put(url__startswith=f"{URL}/refs/{ref}/files/").mock(
            return_value=httpx.Response(200, json={"ref": ref, "rev": rev, "changed": True})
        ),
        respx.delete(url__startswith=f"{URL}/refs/{ref}/files/").mock(
            return_value=httpx.Response(200, json={"ref": ref, "rev": rev, "changed": True})
        ),
        respx.post(f"{URL}/refs/{ref}/commit").mock(
            return_value=httpx.Response(200, json={"ref": ref, "sha": "sha1", "files": []})
        ),
    )


@respx.mock
async def test_every_call_carries_the_token(store):
    tree = model_routes(1, {})
    await store.files()
    assert tree.calls[0].request.headers["authorization"] == "Bearer servicetok"


@respx.mock
async def test_the_whole_model_is_one_listing_plus_one_read_per_file(store):
    model_routes(1, {"cubes/a.yml": "cubes: []\n", "views/b.yml": "views: []\n"})
    assert await store.files() == {"cubes/a.yml": "cubes: []\n", "views/b.yml": "views: []\n"}


@respx.mock
async def test_files_are_cached_so_a_read_does_not_pay_a_round_trip_per_turn(store):
    tree = model_routes(1, {"cubes/a.yml": "x"})
    first = await store.files()
    assert await store.files() == first
    assert tree.call_count == 1


@respx.mock
async def test_a_stale_cache_is_revalidated_against_the_rev_not_the_whole_model(store, monkeypatch):
    monkeypatch.setattr("analyst_agent.modelstore.client.FILES_TTL_S", 0)
    tree = model_routes(1, {"cubes/a.yml": "x"})
    head = ref_route(1)

    await store.files()
    await store.files()
    assert tree.call_count == 1, "an unchanged rev must not refetch the model"
    assert head.call_count == 1


@respx.mock
async def test_a_moved_rev_refetches(store, monkeypatch):
    monkeypatch.setattr("analyst_agent.modelstore.client.FILES_TTL_S", 0)
    model_routes(1, {"cubes/a.yml": "x"})
    ref_route(2)

    assert await store.files() == {"cubes/a.yml": "x"}
    model_routes(2, {"cubes/b.yml": "y"})
    assert await store.files() == {"cubes/b.yml": "y"}


@respx.mock
async def test_a_change_set_is_written_file_by_file_and_committed_once(store):
    put, delete, commit = write_routes("edit-sales")
    ref_route(1, ref="edit-sales")

    await store.edit(
        "edit-sales",
        write={"cubes/a.yml": "x", "views/b.yml": "y"},
        delete=["views/gone.yml"],
        message="one change",
    )
    assert put.call_count == 2
    assert delete.call_count == 1
    assert commit.call_count == 1
    assert b"one change" in commit.calls[0].request.content


@respx.mock
async def test_deletes_land_before_writes(store):
    """A model missing a file compiles; one carrying a stale file beside its replacement does
    not, and every write is live within seconds."""
    seen = []
    respx.delete(url__startswith=f"{URL}/refs/e/files/").mock(
        side_effect=lambda request: (
            seen.append("delete") or httpx.Response(200, json={"rev": 2, "changed": True})
        )
    )
    respx.put(url__startswith=f"{URL}/refs/e/files/").mock(
        side_effect=lambda request: (
            seen.append("write") or httpx.Response(200, json={"rev": 3, "changed": True})
        )
    )
    respx.post(f"{URL}/refs/e/commit").mock(return_value=httpx.Response(200, json={"sha": "s"}))
    ref_route(1, ref="e")

    await store.edit("e", write={"cubes/a.yml": "x"}, delete=["cubes/old.yml"])
    assert seen == ["delete", "write"]


@respx.mock
async def test_a_set_that_changed_nothing_is_not_committed(store):
    """The store refuses an empty commit with 409, and rewriting a file with its own bytes is
    not an edit — so a no-op save must not surface as a failure."""
    respx.put(url__startswith=f"{URL}/refs/e/files/").mock(
        return_value=httpx.Response(200, json={"ref": "e", "rev": 7, "changed": False})
    )
    commit = respx.post(f"{URL}/refs/e/commit")
    ref_route(7, ref="e")

    assert await store.edit("e", write={"cubes/a.yml": "x"}) == 7
    assert not commit.called


@respx.mock
async def test_deleting_a_file_that_is_already_gone_is_the_state_being_asked_for(store):
    respx.delete(f"{URL}/refs/e/files/cubes/old.yml").mock(
        return_value=httpx.Response(404, json={"error": "no such file: cubes/old.yml"})
    )
    commit = respx.post(f"{URL}/refs/e/commit").mock(
        return_value=httpx.Response(200, json={"sha": "s"})
    )
    ref_route(4, ref="e")

    assert await store.edit("e", delete=["cubes/old.yml"]) == 4
    assert not commit.called


@respx.mock
async def test_replace_removes_what_the_new_model_does_not_carry(store):
    model_routes(1, {"cubes/a.yml": "x", "cubes/stale.yml": "y"}, ref="candidate")
    ref_route(1, ref="candidate")
    _, delete, commit = write_routes("candidate")

    await store.replace("candidate", {"cubes/a.yml": "z"}, "rebuild")
    assert delete.calls[0].request.url.path.endswith("/cubes/stale.yml")
    assert commit.call_count == 1


@respx.mock
async def test_a_diff_recovers_the_status_the_store_does_not_report(store):
    """The store answers with changed paths only, and a reviewer reads an add differently from
    a delete."""
    respx.get(f"{URL}/refs/edit/diff").mock(
        return_value=httpx.Response(
            200,
            json={
                "ref": "edit",
                "against": "main",
                "files": ["cubes/new.yml", "cubes/gone.yml", "cubes/a.yml"],
                "patch": "@@ -1 +1 @@",
            },
        )
    )
    model_routes(2, {"cubes/new.yml": "x", "cubes/a.yml": "y"}, ref="edit")
    model_routes(1, {"cubes/gone.yml": "x", "cubes/a.yml": "z"}, ref="main")

    diff = await store.diff("edit")
    assert diff["changed"] == [
        {"status": "A", "path": "cubes/new.yml"},
        {"status": "D", "path": "cubes/gone.yml"},
        {"status": "M", "path": "cubes/a.yml"},
    ]
    assert diff["patch"] == "@@ -1 +1 @@"


@respx.mock
async def test_a_merge_returns_the_revision_main_now_serves(store):
    respx.post(f"{URL}/refs/candidate/merge").mock(
        return_value=httpx.Response(200, json={"ref": "main", "sha": "bbb", "rev": 9})
    )
    assert await store.merge("candidate") == 9


async def test_merging_anywhere_but_main_is_refused_before_the_request(store):
    """The store fast-forwards into prod and nowhere else, so a caller naming another target
    must be told rather than have it land on main."""
    with pytest.raises(StoreError, match="fast-forwards into main only"):
        await store.merge("candidate", "edit-sales")


@respx.mock
async def test_a_missing_ref_is_distinguishable_from_a_store_that_is_down(store):
    """One is a candidate that was cleaned up; the other is an outage, and the editor has to
    say different things about them."""
    respx.get(f"{URL}/refs/gone").mock(
        return_value=httpx.Response(404, json={"error": "unknown ref: gone"})
    )
    with pytest.raises(StoreError) as caught:
        await store.rev("gone")
    assert caught.value.missing and "gone" in str(caught.value)

    respx.get(f"{URL}/refs/main").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(StoreError) as down:
        await store.rev()
    assert not down.value.missing


@respx.mock
async def test_the_stores_own_message_survives_rather_than_a_status_code(store):
    """The store answers `{"error": ...}` where FastAPI would say `detail`, and the reason has
    to reach the agent that tripped it."""
    ref_route(1, ref="e")
    respx.put(f"{URL}/refs/e/files/notes/probe.txt").mock(
        return_value=httpx.Response(400, json={"error": "only .yml/.yaml files may be written"})
    )
    with pytest.raises(StoreError, match="only .yml/.yaml"):
        await store.edit("e", write={"notes/probe.txt": "x"})


@respx.mock
async def test_a_new_ref_names_the_source_the_way_the_store_spells_it(store):
    """`from`, not `from_ref` — the store would silently default to main on the wrong name."""
    route = respx.post(f"{URL}/refs").mock(
        return_value=httpx.Response(
            201, json={"ref": "preview", "sha": "a", "rev": 1, "dirty": False}
        )
    )
    created = await store.create_ref("preview", "edit-sales")
    assert created.name == "preview" and created.rev == 1
    assert b'"from":"edit-sales"' in route.calls[0].request.content


async def test_an_unconfigured_store_is_refused_before_a_request(monkeypatch):
    async def none():
        return Deployment()

    monkeypatch.setattr("analyst_agent.modelstore.client.current", none)
    with pytest.raises(StoreError, match="no model store is configured"):
        await open_store()
