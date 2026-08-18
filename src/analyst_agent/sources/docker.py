"""The Cube container's lifecycle, straight from the source row in analyst.db."""

import shutil
from pathlib import Path

from ..core import proc
from ..core.proc import Result
from .drivers import credentials_for, image_for
from .registry import Source

NETWORK = "analyst-net"
"""Shared with the Cube Store router and worker in docker-compose.yml, which names it
explicitly so this string matches on both sides."""
CUBESTORE_HOST = "ds-cubestore-router"
CUBESTORE_PORT = "3030"
MODEL_MOUNT = "/cube/conf/model"
CONTAINER = "cube-analyst"
"""The one Cube container. A constant rather than a stored name: there is one deployment,
so nothing chooses it and nothing can name a different one."""
DEFAULT_TIMEOUT_S = 60.0
RESTART_POLICY = "unless-stopped"
"""Docker restarts Cube after a reboot. Nothing else would — the container is created by
the portal, not by compose."""


async def run(*args: str, timeout: float = DEFAULT_TIMEOUT_S) -> Result:
    """One docker command. Never raises for a non-zero exit — callers read `ok`."""
    return await proc.run("docker", *args, timeout=timeout)


def installed() -> bool:
    return shutil.which("docker") is not None


async def daemon() -> Result:
    """Whether the daemon answers. Docker Desktop being closed is the common case."""
    return await run("info", "--format", "{{.ServerVersion}}", timeout=15)


async def ensure_network() -> Result:
    """Create the shared network if it is missing. Idempotent."""
    if (await run("network", "inspect", NETWORK, timeout=15)).ok:
        return Result(0, NETWORK, "")
    return await run("network", "create", NETWORK, timeout=30)


# --- the container -----------------------------------------------------------


def argv(
    source: Source,
    model_dir: Path,
    *,
    port: int,
    api_secret: str,
    dev_mode: bool = False,
) -> list[str]:
    """The full `docker run` argument list. Pure, so it can be asserted in a test."""
    spec = source.spec
    environment = {
        **credentials_for(spec, source.credentials),
        "CUBEJS_API_SECRET": api_secret,
        "CUBEJS_DEV_MODE": "true" if dev_mode else "false",
        "CUBEJS_CUBESTORE_HOST": CUBESTORE_HOST,
        "CUBEJS_CUBESTORE_PORT": CUBESTORE_PORT,
    }

    out = [
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--restart",
        RESTART_POLICY,
        "--network",
        NETWORK,
    ]
    for key, value in environment.items():
        out += ["-e", f"{key}={value}"]
    # Bound to loopback, not every interface. With CUBEJS_DEV_MODE on, Cube skips JWT
    # verification entirely, so a published port would answer the whole network
    # unauthenticated. `localhost:<port>` still reaches it, which is all CUBE_URL needs.
    out += ["-p", f"127.0.0.1:{port}:4000", "-v", f"{model_dir}:{MODEL_MOUNT}"]
    for container_path, host_path in source.mounts.items():
        out += ["-v", f"{host_path}:{container_path}:ro"]
    if spec.platform:
        out += ["--platform", spec.platform]
    out.append(image_for(spec))
    return out


async def create(
    source: Source,
    model_dir: Path,
    *,
    port: int,
    api_secret: str,
    dev_mode: bool = False,
) -> Result:
    """Replace the container with a fresh one built from the current row. A container's
    environment cannot be edited, so changing credentials means exactly this."""
    network = await ensure_network()
    if not network.ok:
        return network
    await remove()
    return await run(*argv(source, model_dir, port=port, api_secret=api_secret, dev_mode=dev_mode))


async def start() -> Result:
    return await run("start", CONTAINER)


async def stop() -> Result:
    return await run("stop", CONTAINER, timeout=45)


async def restart() -> Result:
    return await run("restart", CONTAINER, timeout=90)


async def remove(container: str = CONTAINER) -> Result:
    """Named, unlike the other verbs: `modelgen.read` removes its own throwaway probe."""
    return await run("rm", "-f", container, timeout=45)


async def logs(tail: int = 200) -> str:
    """Recent container output. When a warehouse connection fails this is the diagnosis."""
    result = await run("logs", "--tail", str(tail), CONTAINER, timeout=30)
    # Cube writes to both streams; interleaving order is lost either way, and stderr
    # carries the errors, so it goes last where a reader looks first.
    return "\n".join(part for part in (result.out.strip(), result.err.strip()) if part)


async def state() -> str:
    """Docker's state word for the container, or "" when there is none."""
    result = await run(
        "ps",
        "--all",
        "--filter",
        f"name=^{CONTAINER}$",
        "--format",
        "{{.State}}",
        timeout=20,
    )
    return result.out.strip() if result.ok else ""


async def cubestore_running() -> bool:
    """Whether the shared Cube Store is up."""
    result = await run("ps", "--filter", f"name={CUBESTORE_HOST}", "--format", "{{.State}}")
    return result.ok and "running" in result.out
