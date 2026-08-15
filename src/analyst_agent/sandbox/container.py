"""The sandbox container: one for the machine, with every chat's directory mounted into it."""

import asyncio
import hashlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

from ..core import proc
from ..core.config import settings
from ..core.proc import Result
from .scratch import ROOT

log = logging.getLogger("analyst.sandbox")

NAME = "analyst-sandbox"
"""One container, so `docker ps` shows one row however many chats are open. Not `cube-`
prefixed: that prefix means "a source" to `sources.docker.ours`, and this is not one."""
IMAGE_DIR = Path(__file__).parent / "image"
IMAGE_ENV = "ANALYST_SANDBOX_IMAGE"
MOUNT = "/workspace"
"""Where `DATA_DIR/workspaces` lands. A chat works in `MOUNT/<chat_id>`."""

MEMORY = "2g"
"""Shared by every chat, and a cap rather than a reservation — an idle container measures under
a megabyte. 2g and not more because the Docker VM holds the Cube containers too, and this limit
exists to stop one runaway job reaching the ones answering queries."""
PIDS = "512"
"""A fork bomb is the cheapest way to take a machine down from inside a container, and it needs
no privileges at all. Shared, like the memory."""
CPUS = "2"
EXEC_TIMEOUT_S = 120.0
BUILD_TIMEOUT_S = 900.0
"""Installing the analysis stack from wheels, on a cold cache."""

FIXED_ENV = {
    # A read-only root filesystem means every library that wants a home directory has to be
    # pointed at the tmpfs. matplotlib warns on every import otherwise, and pip would try to
    # write a cache into a directory it cannot create.
    "HOME": "/tmp",
    "MPLCONFIGDIR": "/tmp/mpl",
    "PYTHONDONTWRITEBYTECODE": "1",
    # Without it a killed command loses whatever it had buffered — which is the output that
    # would have said why it hung.
    "PYTHONUNBUFFERED": "1",
}
"""Set by `argv` itself and deliberately not a parameter — see the invariants in `__init__`."""


async def _docker(
    *args: str, timeout: float = 60.0, on_output: Callable[[str], None] | None = None
) -> Result:
    """One docker command. Never raises for a non-zero exit — callers read `ok`.

    `sources.docker.run` is the same two lines and stays separate: that package is tier 3 and
    this one is tier 1, so importing it would be an upward import. Neither is a rule that could
    drift; both are `proc.run` with the program filled in.
    """
    return await proc.run("docker", *args, timeout=timeout, on_output=on_output)


def image_tag() -> str:
    """The image to run, tagged by the Dockerfile's own digest — so editing it builds a new
    image instead of silently reusing the last one under the same name."""
    if override := os.environ.get(IMAGE_ENV):
        return override
    digest = hashlib.sha256((IMAGE_DIR / "Dockerfile").read_bytes()).hexdigest()
    return f"analyst-sandbox:{digest[:12]}"


def workdir_for(chat_id: str) -> str:
    """Where a chat's commands run. The container can see its siblings; `--workdir` is what
    keeps a relative path in a script meaning this chat's files."""
    return f"{MOUNT}/{chat_id}"


def argv(workspaces: Path, *, image: str = "") -> list[str]:
    """The full `docker run` argument list. Pure, so the whole isolation contract is a test.

    Note what is not a parameter: an environment variable, a second mount, a network. A
    credential or another directory cannot reach the sandbox by being passed in, only by editing
    this function — the same enforcement the dashboard tools get from having no parameter that
    could carry a layout.
    """
    out = [
        "run",
        "-d",
        "--name",
        NAME,
        # No --restart: a sandbox that survives a reboot is a sandbox nobody reaped. It holds
        # no state — the workspace does — so `ensure_up` making a new one costs 150ms.
        "--network",
        "none",
        # The host's own uid, so files the sandbox writes into the mount are readable by the
        # file tools, and so nothing inside is root even nominally.
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--read-only",
        # Shared between chats, like the memory. The prompt tells the agent to take its own
        # directory with `mktemp -d` rather than assume /tmp is private.
        "--tmpfs",
        "/tmp:rw,size=512m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        MEMORY,
        # Equal to --memory, or the container swaps past the limit instead of being stopped by
        # it, and a runaway job takes the machine's disk with it.
        "--memory-swap",
        MEMORY,
        "--cpus",
        CPUS,
        "--pids-limit",
        PIDS,
        # tini as pid 1. `sleep` cannot reap, and every `docker exec` that outlives its parent
        # is reparented to pid 1 — without this they accumulate as zombies until PIDS is hit,
        # which for a long-lived shared container is a matter of days rather than never.
        "--init",
    ]
    for key, value in FIXED_ENV.items():
        out += ["-e", f"{key}={value}"]
    out += ["-v", f"{workspaces}:{MOUNT}"]
    out.append(image or image_tag())
    # The container exists to be exec'd into; nothing runs in it until a tool asks.
    out += ["sleep", "infinity"]
    return out


def exec_argv(chat_id: str, command: str, timeout: float = EXEC_TIMEOUT_S) -> list[str]:
    """`timeout` runs *inside* the container: killing the `docker exec` client on the host leaves
    the command running, so the kill has to come from the container's own init. TERM first with a
    five-second grace, then KILL, so Python gets to flush what it has printed.
    """
    return [
        "exec",
        "--workdir",
        workdir_for(chat_id),
        NAME,
        "timeout",
        "--kill-after=5",
        f"{timeout:.0f}",
        "bash",
        "-c",
        command,
    ]


def kill_argv(pid: int) -> list[str]:
    """TERM one process inside the sandbox.

    Through `bash -c` because the image has no `/bin/kill` — nor `ps`, `pkill` or `pgrep`, since
    `python:3.12-slim` carries no procps. Getting that wrong is expensive rather than merely
    broken: `docker exec` reports a missing executable as `EXEC_START_FAILED`, which `execute`
    treats as a stale mount and rebuilds the container over, taking every other chat with it.
    """
    return ["exec", NAME, "bash", "-c", f"kill -TERM {pid}"]


async def kill(pid: int) -> Result:
    return await _docker(*kill_argv(pid), timeout=15)


async def state() -> str:
    """docker's state word for the sandbox, or "" when there is no such container. Also how a
    command that killed it is told apart from one that merely failed."""
    result = await _docker("inspect", "-f", "{{.State.Status}}", NAME, timeout=15)
    return result.out.strip() if result.ok else ""


async def ensure_image() -> Result:
    """Build the sandbox image if this Dockerfile's version of it is missing. A no-op on every
    run after the first, and the first is minutes: the container has no network, so everything
    the agent can use has to be baked in."""
    tag = image_tag()
    if (await _docker("image", "inspect", tag, timeout=20)).ok:
        return Result(0, tag, "")
    return await _docker("build", "-t", tag, str(IMAGE_DIR), timeout=BUILD_TIMEOUT_S)


_starting = asyncio.Lock()
"""Serialises `ensure_up`. Startup warms the sandbox in the background while the portal is
already answering, so a first command can arrive mid-build: without this both find no container,
both create one, and the loser's `remove` kills the winner's. A command that waits on a build is
the right outcome — there is nothing else it could do — so the lock is held across it."""


async def ensure_up() -> Result:
    """The sandbox, started if it is not already.

    Checked before every command rather than once, because there are four ordinary ways for it to
    be gone: the machine rebooted, the portal restarted, the startup warm has not finished, or a
    command in some other chat exhausted the shared memory limit and took it down. None of them
    loses anything, because the workspace is the state.
    """
    async with _starting:
        if await state() == "running":
            return Result(0, NAME, "")
        if not (built := await ensure_image()).ok:
            return built
        # An exited container still owns the name, so a plain `run` would fail on it.
        await remove()
        return await _docker(*argv(settings.dir_path / ROOT))


async def rebuild() -> Result:
    """Drop the container and build a current one.

    `remove` before `ensure_up` and not `ensure_up` alone: a container that is *running* — left by
    a previous process, or holding a mount whose host directory has since been replaced — is one
    `ensure_up` finds healthy and keeps, along with whatever settings and mount it was built with.
    Both callers need exactly that ordering, which is why they share this rather than each
    remembering it: startup, and the stale-mount recovery in `execute`.
    """
    await remove()
    result = await ensure_up()
    if not result.ok:
        # Backgrounded at startup, so this is the only place a failure is visible until someone
        # runs a command. 127 here means docker is not installed, which is a normal state for
        # someone who only reads dashboards.
        log.warning("sandbox is not available: %s", result.message)
    return result


EXEC_START_FAILED = "OCI runtime exec failed"
"""docker could not start the process at all, which is a different thing from the command
failing. The way this happens in practice: `DATA_DIR/workspaces` was deleted and recreated on
the host, so the running container's bind mount still points at the old inode and no chat's
directory exists inside it. Every command then fails this way until the container is rebuilt.

**docker reports it on stdout, not stderr,** with exit 127 — so a check against `err` alone
matches nothing and the recovery below never runs.
"""


async def execute(
    chat_id: str,
    command: str,
    timeout: float = EXEC_TIMEOUT_S,
    on_output: Callable[[str], None] | None = None,
) -> Result:
    """Run one command in a chat's directory. Exit 124 is the container's `timeout` giving up on
    it; 137 means killed outright, by that timeout's second signal or by the memory limit; 143 is
    a TERM from `kill`, which is the stop button."""
    # The host timeout is longer on purpose: it exists to stop us waiting on a docker client that
    # has itself wedged, not to stop the command.
    argv = exec_argv(chat_id, command, timeout)
    result = await _docker(*argv, timeout=timeout + 10, on_output=on_output)
    if EXEC_START_FAILED not in result.out + result.err:
        return result

    # Rebuild against the mount as it is now, then try the command once more.
    if not (up := await rebuild()).ok:
        return up
    return await _docker(*argv, timeout=timeout + 10, on_output=on_output)


async def remove() -> Result:
    """Drop the container. Called at startup and by nothing else: it holds no state, so removing
    it is only ever a way of guaranteeing the next one is built from current settings."""
    return await _docker("rm", "-f", NAME, timeout=45)
