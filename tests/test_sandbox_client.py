"""The portal's client for the sandbox service. No service, no containers — respx answers.

The sibling of `test_modelstore.py`, and for the same reason: three services, three clients, and
what can go wrong in each is the wire rather than the work.
"""

import httpx
import pytest
import respx

from analyst_agent.core.deployment import Deployment
from analyst_agent.sandbox import SandboxError, open_sandbox
from analyst_agent.sandbox.client import SandboxClient

URL = "http://sandbox:8600"


@pytest.fixture
async def sandbox():
    async with SandboxClient(URL, "tok") as client:
        yield client


def stream(*lines: str):
    return httpx.Response(200, content="".join(f"{line}\n" for line in lines))


@respx.mock
async def test_every_call_carries_the_token(sandbox):
    route = respx.post(f"{URL}/kill").mock(return_value=httpx.Response(200, json={"killed": True}))
    await sandbox.kill("c1")
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_output_arrives_as_it_streams_and_the_result_comes_last(sandbox):
    """The whole reason this is a stream: the live output panel has to fill while the script is
    still running, not after it exits."""
    respx.post(f"{URL}/run").mock(
        return_value=stream(
            '{"output": "half "}',
            '{"output": "a line"}',
            '{"result": {"code": 0, "out": "half a line", "script": "scripts/run-001.py"}}',
        )
    )
    seen: list[str] = []
    result = await sandbox.run("c1", "print(1)", on_output=seen.append)

    assert seen == ["half ", "a line"]
    assert result.ok and result.script == "scripts/run-001.py"


@respx.mock
async def test_a_stream_that_ends_with_no_result_is_an_error_not_a_success(sandbox):
    """A run reported as exit 0 because the service died mid-stream would have the agent answer
    from output it never got."""
    respx.post(f"{URL}/run").mock(return_value=stream('{"output": "started"}'))
    with pytest.raises(SandboxError, match="no result"):
        await sandbox.run("c1", "print(1)")


@respx.mock
async def test_a_run_reports_what_it_wrote(sandbox):
    respx.post(f"{URL}/run").mock(
        return_value=stream('{"result": {"code": 0, "wrote": ["chart.png"], "expired": false}}')
    )
    assert (await sandbox.run("c1", "x")).wrote == ["chart.png"]


@respx.mock
async def test_a_service_that_is_not_answering_says_so(sandbox):
    respx.post(f"{URL}/run").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(SandboxError, match="did not answer"):
        await sandbox.run("c1", "x")


@respx.mock
async def test_a_missing_file_keeps_its_status(sandbox):
    """404 rather than 502, so the download route answers the browser the same way the service
    answered us."""
    respx.get(f"{URL}/files/c1/nope.csv").mock(return_value=httpx.Response(404))
    with pytest.raises(SandboxError) as caught:
        await sandbox.read("c1", "nope.csv")
    assert caught.value.status == 404


@respx.mock
async def test_an_upload_comes_back_with_the_path_the_service_chose(sandbox):
    """The name is the caller's and the path is the service's — it owns the directory, so it
    decides where a browser-supplied filename lands."""
    respx.post(f"{URL}/uploads/c1").mock(
        return_value=httpx.Response(200, json={"path": "uploads/orders-2.csv"})
    )
    assert await sandbox.upload("c1", "../orders.csv", b"a,b\n") == "uploads/orders-2.csv"


async def test_an_unconfigured_sandbox_is_refused_before_a_request(monkeypatch):
    """Optional, unlike Cube: a deployment with no sandbox answers questions and just cannot run
    Python, so this is something callers check rather than a fatal state."""

    async def none():
        return Deployment()

    monkeypatch.setattr("analyst_agent.sandbox.client.current", none)
    with pytest.raises(SandboxError) as caught:
        await open_sandbox()
    assert caught.value.unconfigured
