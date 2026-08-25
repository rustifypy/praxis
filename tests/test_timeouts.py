"""The caps on both sides of the seam, and which one fires first.

The smaller cap is the one whose error a person sees, so the choice per call is who reports.
Cube-bound: the portal, because the store answers 502 and that says nothing useful. Sandbox runs
and uploads: the service, because it spawns and saves, and a client giving up first abandons a
success.

Neither service's source is read: both are published images built elsewhere. So both ceilings are
ones the portal *sets* in the hand-over it renders, which is what keeps this an assertion about a
running deployment rather than about a directory that happens to sit alongside.
"""

import pytest

from analyst_agent.core.config import settings
from analyst_agent.core.deployenv import (
    CUBE_TIMEOUT_S,
    SANDBOX_EXEC_TIMEOUT_S,
    SANDBOX_PROFILE_TIMEOUT_S,
)
from analyst_agent.modelstore.client import ADMIN_TIMEOUT_S, POLL_REQUEST_TIMEOUT_S
from analyst_agent.modelstore.modelgen.read import PROBE_QUERY_TIMEOUT_S
from analyst_agent.sandbox import client
from analyst_agent.sandbox.client import RUN_TIMEOUT_S, UPLOAD_TIMEOUT_S
from analyst_agent.web.reachable import CHECK_TIMEOUT_S, LIVE_TIMEOUT_S

# The sets are the list of what crosses the seam, so a new cap has one place to be added.
CUBE_BOUND = {
    "settings.query_timeout_s": settings.query_timeout_s,
    "cube.ADMIN_TIMEOUT_S": ADMIN_TIMEOUT_S,
    "cube.POLL_REQUEST_TIMEOUT_S": POLL_REQUEST_TIMEOUT_S,
    "read.PROBE_QUERY_TIMEOUT_S": PROBE_QUERY_TIMEOUT_S,
    "reachable.CHECK_TIMEOUT_S": CHECK_TIMEOUT_S,
    "reachable.LIVE_TIMEOUT_S": LIVE_TIMEOUT_S,
}

SERVICE_REPORTS = {
    "sandbox.RUN_TIMEOUT_S": (RUN_TIMEOUT_S, "SANDBOX_EXEC_TIMEOUT_S", SANDBOX_EXEC_TIMEOUT_S),
    "sandbox.UPLOAD_TIMEOUT_S": (
        UPLOAD_TIMEOUT_S,
        "SANDBOX_PROFILE_TIMEOUT_S",
        SANDBOX_PROFILE_TIMEOUT_S,
    ),
}


@pytest.mark.parametrize("name", sorted(CUBE_BOUND))
def test_a_cube_bound_cap_gives_up_before_the_store_does(name):
    assert CUBE_BOUND[name] < CUBE_TIMEOUT_S, (
        f"{name} is {CUBE_BOUND[name]}s against the {CUBE_TIMEOUT_S}s cap the hand-over gives "
        "the store — it can never elapse, so a slow query is reported as a proxy failure instead"
    )


@pytest.mark.parametrize("name", sorted(SERVICE_REPORTS))
def test_the_sandbox_outlives_nothing_it_started(name):
    ours, theirs, floor = SERVICE_REPORTS[name]
    assert ours > floor, (
        f"{name} is {ours}s against the {theirs} of {floor}s the hand-over gives the sandbox — "
        "the client gives up first, so a call that succeeded on the far side is reported here "
        "as a failure"
    )


def test_the_upload_cap_covers_a_whole_file_as_well_as_its_profile():
    """The profiler's cap starts once the body has arrived, so one second above it is inverted
    for any file big enough to matter."""
    assert UPLOAD_TIMEOUT_S >= SANDBOX_PROFILE_TIMEOUT_S * 2


def test_the_default_client_cap_pairs_with_nothing():
    """`TIMEOUT_S` covers calls with no cap on the far side, so it is patience, not a rung."""
    assert client.TIMEOUT_S not in {ours for ours, _, _ in SERVICE_REPORTS.values()}
    assert client.UPLOAD_TIMEOUT_S != client.TIMEOUT_S
