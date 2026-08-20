"""The portal's client for the model store. No store, no containers — respx answers."""

import httpx
import pytest
import respx

from analyst_agent.core.deployment import Deployment
from analyst_agent.modelstore import ModelStore, StoreError, open_store

URL = "http://store:8500"


@pytest.fixture
async def store():
    async with ModelStore(URL, "writetok") as client:
        yield client


def files_route(sha: str, files: dict, ref: str = "main"):
    return respx.get(f"{URL}/files", params={"ref": ref}).mock(
        return_value=httpx.Response(200, json={"ref": ref, "sha": sha, "files": files})
    )


def version_route(sha: str, ref: str = "main"):
    return respx.get(f"{URL}/version", params={"ref": ref}).mock(
        return_value=httpx.Response(200, json={"ref": ref, "sha": sha})
    )


@respx.mock
async def test_every_call_carries_the_token(store):
    route = files_route("abc", {})
    await store.files()
    assert route.calls[0].request.headers["authorization"] == "Bearer writetok"


@respx.mock
async def test_files_are_cached_so_grounding_does_not_pay_a_round_trip_per_turn(store):
    """It is read on every chat turn, and the model changes when someone presses a button."""
    route = files_route("abc", {"cubes/a.yml": "cubes: []\n"})
    first = await store.files()
    assert await store.files() == first
    assert route.call_count == 1


@respx.mock
async def test_a_stale_cache_is_revalidated_against_the_sha_not_the_whole_model(store, monkeypatch):
    monkeypatch.setattr("analyst_agent.modelstore.client.FILES_TTL_S", 0)
    files = files_route("abc", {"cubes/a.yml": "x"})
    version = version_route("abc")

    await store.files()
    await store.files()
    assert files.call_count == 1, "unchanged sha must not refetch the model"
    assert version.call_count == 1


@respx.mock
async def test_a_moved_sha_refetches(store, monkeypatch):
    monkeypatch.setattr("analyst_agent.modelstore.client.FILES_TTL_S", 0)
    files_route("abc", {"cubes/a.yml": "x"})
    version_route("def")

    assert await store.files() == {"cubes/a.yml": "x"}
    respx.get(f"{URL}/files", params={"ref": "main"}).mock(
        return_value=httpx.Response(200, json={"ref": "main", "sha": "def", "files": {"b": "y"}})
    )
    assert await store.files() == {"b": "y"}


@respx.mock
async def test_a_write_drops_the_cache_it_would_otherwise_contradict(store):
    files_route("abc", {"cubes/a.yml": "x"})
    respx.patch(f"{URL}/files", params={"ref": "main"}).mock(
        return_value=httpx.Response(200, json={"ref": "main", "sha": "def"})
    )
    await store.files()
    await store.edit("main", write={"cubes/b.yml": "y"})
    files_route("def", {"cubes/a.yml": "x", "cubes/b.yml": "y"})
    assert sorted(await store.files()) == ["cubes/a.yml", "cubes/b.yml"]


@respx.mock
async def test_replace_sends_the_whole_set_in_one_commit(store):
    route = respx.put(f"{URL}/files", params={"ref": "candidate"}).mock(
        return_value=httpx.Response(200, json={"ref": "candidate", "sha": "aaa"})
    )
    sha = await store.replace("candidate", {"cubes/a.yml": "x"}, "cubes — 1 cube")
    assert sha == "aaa"
    body = httpx.Request("PUT", "x", content=route.calls[0].request.content).content
    assert b"cubes/a.yml" in body and b"cubes \xe2\x80\x94 1 cube" in body


@respx.mock
async def test_a_merge_returns_the_new_head(store):
    respx.post(f"{URL}/merge").mock(
        return_value=httpx.Response(200, json={"into": "main", "sha": "bbb"})
    )
    assert await store.merge("candidate") == "bbb"


@respx.mock
async def test_a_missing_ref_is_distinguishable_from_a_store_that_is_down(store):
    """One is a candidate that was cleaned up; the other is an outage, and the editor has to
    say different things about them."""
    respx.get(f"{URL}/version", params={"ref": "gone"}).mock(
        return_value=httpx.Response(404, json={"detail": "no such ref: 'gone'"})
    )
    with pytest.raises(StoreError) as caught:
        await store.version("gone")
    assert caught.value.missing and "gone" in str(caught.value)

    respx.get(f"{URL}/version", params={"ref": "main"}).mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(StoreError) as down:
        await store.version()
    assert not down.value.missing


@respx.mock
async def test_the_stores_own_message_survives_rather_than_a_status_code(store):
    """The path allowlist and the YAML check are the store's, and the reason has to reach the
    agent that tripped it."""
    respx.patch(f"{URL}/files", params={"ref": "main"}).mock(
        return_value=httpx.Response(
            400, json={"detail": "'grounding/probe.yml' must be cubes/*.yml, views/*.yml"}
        )
    )
    with pytest.raises(StoreError, match="grounding/probe.yml"):
        await store.edit("main", write={"grounding/probe.yml": "x"})


async def test_an_unconfigured_store_is_refused_before_a_request(monkeypatch):
    async def none():
        return Deployment()

    monkeypatch.setattr("analyst_agent.modelstore.client.current", none)
    with pytest.raises(StoreError, match="no model store is configured"):
        await open_store()
