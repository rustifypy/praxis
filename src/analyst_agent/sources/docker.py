"""Container lifecycle for one source, straight from its row in sources.db."""

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
PREFIX = "cube-"
"""Container name prefix, so `ours()` can tell a source container from anything else
on the machine."""
DEFAULT_TIMEOUT_S = 60.0
RESTART_POLICY = "unless-stopped"
"""Docker restarts sources after a reboot. Nothing else would — there is no `make up`
for sources any more."""


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


# --- one source's container -------------------------------------------------


def argv(source: Source, model_dir: Path, *, api_secret: str, dev_mode: bool = False) -> list[str]:
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
        source.service,
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
    out += ["-p", f"127.0.0.1:{source.port}:4000", "-v", f"{model_dir}:{MODEL_MOUNT}"]
    for container_path, host_path in source.mounts.items():
        out += ["-v", f"{host_path}:{container_path}:ro"]
    if spec.platform:
        out += ["--platform", spec.platform]
    out.append(image_for(spec))
    return out


async def create(source: Source, model_dir: Path, *, api_secret: str, dev_mode: bool = False):
    """Replace this source's container with a fresh one built from its current row."""
    network = await ensure_network()
    if not network.ok:
        return network
    await remove(source.service)
    return await run(*argv(source, model_dir, api_secret=api_secret, dev_mode=dev_mode))


async def start(container: str) -> Result:
    return await run("start", container)


async def stop(container: str) -> Result:
    return await run("stop", container, timeout=45)


async def restart(container: str) -> Result:
    return await run("restart", container, timeout=90)


async def remove(container: str) -> Result:
    return await run("rm", "-f", container, timeout=45)


async def logs(container: str, tail: int = 200) -> str:
    """Recent container output. When a warehouse connection fails this is the diagnosis."""
    result = await run("logs", "--tail", str(tail), container, timeout=30)
    # Cube writes to both streams; interleaving order is lost either way, and stderr
    # carries the errors, so it goes last where a reader looks first.
    return "\n".join(part for part in (result.out.strip(), result.err.strip()) if part)


async def ours() -> dict[str, str]:
    """Container name -> docker's state word, for every `cube-*` container."""
    result = await run(
        "ps",
        "--all",
        "--filter",
        f"name=^{PREFIX}",
        "--format",
        "{{.Names}}\t{{.State}}",
        timeout=20,
    )
    if not result.ok:
        return {}
    out = {}
    for line in result.out.splitlines():
        name, _, state = line.partition("\t")
        if name.strip():
            out[name.strip()] = state.strip()
    return out


async def cubestore_running() -> bool:
    """Whether the shared Cube Store is up."""
    result = await run("ps", "--filter", f"name={CUBESTORE_HOST}", "--format", "{{.State}}")
    return result.ok and "running" in result.out


def orphans(containers: dict[str, str], known: list[str]) -> list[str]:
    """`cube-*` containers with no source row — usually a removal that half-finished."""
    expected = {f"{PREFIX}{name}" for name in known}
    return sorted(name for name in containers if name.startswith(PREFIX) and name not in expected)
