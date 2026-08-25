"""`publish.install` — the candidate ref, which is what makes a rebuild non-destructive.

Re-running the cubes step used to unlink every `*.yml` in the served model and write the new
one over it, so a model Cube then refused left nothing running. Now the rebuild is a commit on
a ref nothing is querying, and `ref` moves only once Cube has compiled it. No store and no
container: respx answers, and `check` is stubbed to be the verdict.
"""

import httpx
import pytest
import respx

from analyst_agent.core.deployment import Deployment
from analyst_agent.modelstore.modelgen import publish

URL = "http://store:8000"
FILES = {"cubes/album.yml": "cubes:\n- name: album\n"}


@pytest.fixture
def configured(monkeypatch):
    async def deployment():
        return Deployment(store_url=URL, store_token="w")

    monkeypatch.setattr("analyst_agent.modelstore.client.current", deployment)


@pytest.fixture
def verdict(monkeypatch):
    """`check` is the only thing that decides, so the test sets it and watches the writes."""
    seen: dict = {}

    def compiles(compiled: bool):
        async def check(**kwargs):
            seen.update(kwargs)
            return publish.Validation(compiled=compiled, entities=["album"])

        monkeypatch.setattr(publish, "check", check)
        return seen

    return compiles


def routes(
    candidate_exists: bool = False,
    carries: dict | None = None,
    write_answer: httpx.Response | None = None,
):
    """The store as `install` uses it, and the calls back so order can be asserted.

    A replace is a listing, one write per file, and one commit — the store takes no bulk write,
    so `carries` is what the candidate already holds and therefore what has to be removed.
    """
    listed = [{"path": p, "size": 1, "modified": 0.0} for p in carries or {}]
    respx.get(f"{URL}/refs/{publish.CANDIDATE}/tree").mock(
        return_value=httpx.Response(200, json={"ref": publish.CANDIDATE, "rev": 1, "files": listed})
    )
    respx.get(f"{URL}/refs/{publish.CANDIDATE}").mock(
        return_value=httpx.Response(
            200, json={"ref": publish.CANDIDATE, "sha": "aaa", "rev": 1, "dirty": False}
        )
    )
    return {
        "delete": respx.delete(f"{URL}/refs/{publish.CANDIDATE}").mock(
            return_value=httpx.Response(204)
            if candidate_exists
            else httpx.Response(404, json={"error": "unknown ref: candidate"})
        ),
        "create": respx.post(f"{URL}/refs").mock(
            return_value=httpx.Response(
                201, json={"ref": publish.CANDIDATE, "sha": "aaa", "rev": 1, "dirty": False}
            )
        ),
        "replace": respx.put(url__startswith=f"{URL}/refs/{publish.CANDIDATE}/files/").mock(
            return_value=write_answer
            or httpx.Response(200, json={"ref": publish.CANDIDATE, "rev": 2, "changed": True})
        ),
        "commit": respx.post(f"{URL}/refs/{publish.CANDIDATE}/commit").mock(
            return_value=httpx.Response(
                200, json={"ref": publish.CANDIDATE, "sha": "bbb", "files": []}
            )
        ),
        "merge": respx.post(f"{URL}/refs/{publish.CANDIDATE}/merge").mock(
            return_value=httpx.Response(200, json={"ref": "main", "sha": "ccc", "rev": 3})
        ),
    }


@respx.mock
async def test_a_model_that_compiles_lands_on_main(configured, verdict):
    seen = verdict(True)
    call = routes()

    result = await publish.install(FILES, message="cubes — 1 cube")

    assert result.compiled and not result.candidate
    assert call["create"].called and call["replace"].called
    # Validated at the candidate, never at main: main is what everything else is querying.
    assert seen["ref"] == publish.CANDIDATE
    assert call["merge"].called
    # The ref being merged is the route, not a field in a body.
    assert call["merge"].calls[0].request.url.path == f"/refs/{publish.CANDIDATE}/merge"
    # Deleted twice: the leftover check on the way in, and the cleanup on the way out.
    assert call["delete"].call_count == 2


@respx.mock
async def test_a_model_cube_refuses_never_reaches_main(configured, verdict):
    """The whole point. Nothing that answers a query has moved, and the rejected YAML is still
    readable at the ref the failure names."""
    verdict(False)
    call = routes()

    result = await publish.install(FILES)

    assert not result.compiled
    assert result.candidate == publish.CANDIDATE
    assert not call["merge"].called
    # Left in place rather than cleaned up — until the next attempt replaces it.
    assert call["delete"].call_count == 1


@respx.mock
async def test_a_leftover_candidate_is_deleted_rather_than_written_on_top_of(configured, verdict):
    """A candidate from a previous rejection has diverged from main. Committing onto it would
    make the merge a real three-way one, which is how a stale side comes quietly back."""
    verdict(True)
    call = routes(candidate_exists=True)

    await publish.install(FILES)

    assert call["delete"].calls[0].request.url.path.endswith(publish.CANDIDATE)
    assert call["create"].called, "recreated from main, so the merge is the candidate's own tree"


@respx.mock
async def test_a_store_that_will_not_take_the_model_is_not_reported_as_a_compile_error(
    configured, verdict
):
    """A path the store refuses is a bug in what was emitted, and it has to arrive with the
    store's own reason rather than as "Cube rejected the model"."""
    verdict(True)
    routes(write_answer=httpx.Response(400, json={"error": "'cubes/a.txt' must be cubes/*.yml"}))
    with pytest.raises(Exception, match="cubes/a.txt"):
        await publish.install({"cubes/a.txt": "x"})
