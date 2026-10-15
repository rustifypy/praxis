"""Both reachability questions, over respx. Nothing here needs a container.

The two halves are tested apart on purpose: `semantic`/`sandbox` are asked once about a pasted
address and may warn without refusing, while `querying`/`alive` are asked repeatedly about the
row and must be cheap. A test that could not tell them apart would let them be merged.
"""

import httpx
import pytest
import respx

from praxis.api.onboarding import reachable
from praxis.core.deployment import CONTRACT, Deployment

STORE = "http://localhost:8000"
SANDBOX = "http://localhost:8600"
TOKEN = "w" * 40

HEALTHY_STORE = {"status": "ok"}
SERVING = f"{STORE}/refs/main/meta"
META = {"cubes": [{"name": "orders"}]}
HEALTHY_SANDBOX = {"ok": True, "contract": CONTRACT, "root": "/data", "running": 0}


def sandboxed(**over) -> Deployment:
    return Deployment(sandbox_url=SANDBOX, sandbox_token=TOKEN, **over)


# --- the full chain: is this the deployment I think it is? --------------------


@respx.mock
async def test_semantic_passes_when_both_halves_answer():
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200, json=HEALTHY_STORE))
    respx.get(SERVING).mock(return_value=httpx.Response(200, json={"health": "HEALTH"}))
    found = await reachable.semantic(STORE, TOKEN)
    assert found.ok and found.status == "ok" and not found.warnings


@respx.mock
async def test_semantic_refuses_when_nothing_is_listening():
    respx.get(f"{STORE}/healthz").mock(side_effect=httpx.ConnectError("refused"))
    found = await reachable.semantic(STORE, TOKEN)
    assert not found.ok and found.status == "unreachable"
    assert "nothing listening" in found.detail


@respx.mock
async def test_semantic_refuses_a_store_that_answers_badly():
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(503))
    found = await reachable.semantic(STORE, TOKEN)
    assert not found.ok and found.status == "unreachable"
    assert "answered 503" in found.detail


@respx.mock
async def test_something_else_on_the_port_is_not_a_store():
    """A 200 of prose is the failure that would otherwise read as a healthy deployment."""
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200, text="<html>nginx</html>"))
    found = await reachable.semantic(STORE, TOKEN)
    assert not found.ok and found.status == "wrong service"
    assert "not as the model store" in found.detail


@respx.mock
async def test_the_store_is_not_asked_for_a_contract_it_does_not_report():
    """It is a separate product with its own release cycle and no version handshake, so a
    warning here would invent agreement rather than confirm it. The sandbox is ours and still
    speaks one."""
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200, json=HEALTHY_STORE))
    respx.get(SERVING).mock(return_value=httpx.Response(200, json=META))
    found = await reachable.semantic(STORE, TOKEN)
    assert found.ok and not found.warnings


@respx.mock
async def test_a_rejected_token_blames_the_file_and_not_the_warehouse():
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200, json=HEALTHY_STORE))
    respx.get(SERVING).mock(return_value=httpx.Response(401))
    found = await reachable.semantic(STORE, TOKEN)
    assert not found.ok and found.status == "rejected"
    assert "rendered for a different connection" in found.detail


@respx.mock
async def test_a_store_that_is_up_with_cube_still_booting_is_refused():
    """The half-broken state the second leg exists for: stopping at `/health` passes onboarding
    and then fails every question."""
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200, json=HEALTHY_STORE))
    respx.get(SERVING).mock(return_value=httpx.Response(502, json={"error": "no datasource"}))
    found = await reachable.semantic(STORE, TOKEN)
    assert not found.ok and found.status == "not ready"
    assert "Cube is not ready behind the store" in found.detail
    assert "no datasource" in found.detail


@respx.mock
async def test_a_ref_with_nothing_published_is_not_reported_as_an_outage():
    """409 is the store refusing before Cube is involved, so blaming Cube — or the warehouse —
    for a model that was never built is what this branch exists to prevent."""
    respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200, json=HEALTHY_STORE))
    respx.get(SERVING).mock(
        return_value=httpx.Response(409, json={"error": "ref main has no revision to serve yet"})
    )
    found = await reachable.semantic(STORE, TOKEN)
    assert not found.ok and found.status == "not ready"
    assert "no revision to serve yet" in found.detail
    assert "Cube is not ready" not in found.detail


@respx.mock
async def test_sandbox_passes_when_the_guarded_route_answers():
    respx.get(f"{SANDBOX}/health").mock(return_value=httpx.Response(200, json=HEALTHY_SANDBOX))
    respx.get(f"{SANDBOX}/profiles/reachability-probe").mock(
        return_value=httpx.Response(200, json={})
    )
    found = await reachable.sandbox(SANDBOX, TOKEN)
    assert found.ok and not found.warnings


@respx.mock
async def test_an_open_health_route_cannot_prove_the_sandbox_token():
    """`/health` needs no token, so a wrong one passes it and dies on the first script."""
    respx.get(f"{SANDBOX}/health").mock(return_value=httpx.Response(200, json=HEALTHY_SANDBOX))
    respx.get(f"{SANDBOX}/profiles/reachability-probe").mock(return_value=httpx.Response(401))
    found = await reachable.sandbox(SANDBOX, TOKEN)
    assert not found.ok and found.status == "rejected"
    assert "the sandbox rejected" in found.detail


@respx.mock
async def test_a_sandbox_contract_mismatch_mentions_scripts():
    """The same warning, worded for what will actually go wrong here."""
    respx.get(f"{SANDBOX}/health").mock(
        return_value=httpx.Response(200, json={"ok": True, "contract": CONTRACT + 1})
    )
    respx.get(f"{SANDBOX}/profiles/reachability-probe").mock(
        return_value=httpx.Response(200, json={})
    )
    found = await reachable.sandbox(SANDBOX, TOKEN)
    assert found.ok and "scripts fail oddly" in found.warnings[0]


# --- the cheap leg: can I use it right now? ----------------------------------


@respx.mock
async def test_querying_passes_when_cube_answers_for_main(deployment):
    respx.get(SERVING).mock(return_value=httpx.Response(200, json=META))
    found = await reachable.querying(deployment)
    assert found.ok and found.status == "ok"


@respx.mock
async def test_querying_reports_down_with_detail(deployment):
    respx.get(SERVING).mock(return_value=httpx.Response(502, json={"error": "cube unreachable"}))
    found = await reachable.querying(deployment)
    assert not found.ok and found.status == "502"
    assert "Cube is not ready behind the store" in found.detail
    assert "cube unreachable" in found.detail


@respx.mock
async def test_querying_survives_a_connection_reset_while_booting(deployment):
    """A container that accepts then resets raises ReadError, not ConnectError."""
    respx.get(SERVING).mock(side_effect=httpx.ReadError("reset"))
    found = await reachable.querying(deployment)
    assert not found.ok and found.status == "unreachable"
    assert "ReadError" in found.detail


@respx.mock
async def test_a_rejected_token_is_not_reported_as_a_warehouse_outage(deployment):
    """The bug this consolidation existed to fix: a 401 fell through to the default detail and
    blamed the database for a file rendered against another connection."""
    respx.get(SERVING).mock(return_value=httpx.Response(401))
    found = await reachable.querying(deployment)
    assert not found.ok and found.status == "rejected"
    assert "rendered for a different connection" in found.detail


async def test_an_unconfigured_deployment_is_its_own_state():
    """Nothing to probe is not the same as something that will not answer, and an address is
    only set once someone has been through onboarding."""
    found = await reachable.querying(Deployment())
    assert not found.ok and found.status == "unconfigured"


@respx.mock
async def test_the_recurring_probe_asks_once(deployment):
    """It is answered on every page build, so a second leg is a cost paid forever. Asking the
    store for main's metadata is what makes one request prove three things."""
    store = respx.get(f"{STORE}/healthz").mock(return_value=httpx.Response(200))
    cube = respx.get(SERVING).mock(return_value=httpx.Response(200, json=META))
    await reachable.querying(deployment)
    assert cube.called and not store.called


@respx.mock
async def test_alive_needs_no_token():
    """Its one reader is a banner. The token is the wizard's to check, not every page load's."""
    health = respx.get(f"{SANDBOX}/health").mock(
        return_value=httpx.Response(200, json=HEALTHY_SANDBOX)
    )
    guarded = respx.get(f"{SANDBOX}/profiles/reachability-probe")
    found = await reachable.alive(sandboxed())
    assert found.ok
    assert health.called and not guarded.called
    assert "authorization" not in {k.lower() for k in health.calls[0].request.headers}


@respx.mock
async def test_alive_still_checks_it_is_a_sandbox():
    """Something else listening on that port is the one identity failure worth a round trip."""
    respx.get(f"{SANDBOX}/health").mock(return_value=httpx.Response(200, json={"nginx": True}))
    found = await reachable.alive(sandboxed())
    assert not found.ok and found.status == "wrong service"


async def test_no_sandbox_is_not_a_broken_sandbox():
    """It is optional, so "none configured" has to read differently from "yours is down"."""
    found = await reachable.alive(Deployment())
    assert not found.ok and found.status == "unconfigured"
    assert "no sandbox is configured" in found.detail


# --- the wizard's dispatcher --------------------------------------------------


async def test_a_blank_address_is_refused_without_a_request():
    found = await reachable.test(Deployment(), "semantic", "   ")
    assert not found.ok and found.detail == "no address"


async def test_an_unknown_service_is_a_programming_error():
    """Not a `Reached`: the two services are named in one dict on the route, so reaching here
    with a third means the route and this module disagree."""
    with pytest.raises(ValueError, match="no such service"):
        await reachable.test(Deployment(), "warehouse", "http://localhost:1")


@respx.mock
async def test_the_dispatcher_tests_the_pasted_address_not_the_row(deployment):
    """The row still holds the old address while the wizard checks a new one."""
    elsewhere = "http://elsewhere:9000"
    respx.get(f"{elsewhere}/healthz").mock(return_value=httpx.Response(200, json=HEALTHY_STORE))
    respx.get(f"{elsewhere}/refs/main/meta").mock(return_value=httpx.Response(200, json=META))
    stale = respx.get(f"{STORE}/healthz")
    found = await reachable.test(deployment, "semantic", elsewhere)
    assert found.ok and not stale.called
