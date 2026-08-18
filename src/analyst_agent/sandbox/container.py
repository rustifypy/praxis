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
IMAGE_DIR = Path(__file__).parent / "image"
IMAGE_ENV = "ANALYST_SANDBOX_IMAGE"
MOUNT = "/workspace"
MEMORY = "2g"
PIDS = "512"
CPUS = "2"
EXEC_TIMEOUT_S = 120.0
BUILD_TIMEOUT_S = 900.0

EXEC_START_FAILED = "OCI runtime exec failed"

FIXED_ENV = {
    "HOME": "/tmp",
    "MPLCONFIGDIR": "/tmp/mpl",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}


async def _docker(
    *args: str, timeout: float = 60.0, on_output: Callable[[str], None] | None = None
) -> Result:
    return await proc.run("docker", *args, timeout=timeout, on_output=on_output)


def image_tag() -> str:
    if override := os.environ.get(IMAGE_ENV):
        return override
    digest = hashlib.sha256((IMAGE_DIR / "Dockerfile").read_bytes()).hexdigest()
    return f"analyst-sandbox:{digest[:12]}"


def workdir_for(chat_id: str) -> str:
    return f"{MOUNT}/{chat_id}"


def argv(workspaces: Path, *, image: str = "") -> list[str]:
    out = [
        "run",
        "-d",
        "--name",
        NAME,
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,size=512m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        MEMORY,
        "--memory-swap",
        MEMORY,
        "--cpus",
        CPUS,
        "--pids-limit",
        PIDS,
        "--init",
    ]
    for key, value in FIXED_ENV.items():
        out += ["-e", f"{key}={value}"]
    out += ["-v", f"{workspaces}:{MOUNT}"]
    out.append(image or image_tag())
    out += ["sleep", "infinity"]
    return out


def exec_argv(chat_id: str, command: str, timeout: float = EXEC_TIMEOUT_S) -> list[str]:
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
    return ["exec", NAME, "bash", "-c", f"kill -TERM {pid}"]


async def kill(pid: int) -> Result:
    return await _docker(*kill_argv(pid), timeout=15)


async def state() -> str:
    result = await _docker("inspect", "-f", "{{.State.Status}}", NAME, timeout=15)
    return result.out.strip() if result.ok else ""


async def ensure_image() -> Result:
    tag = image_tag()
    if (await _docker("image", "inspect", tag, timeout=20)).ok:
        return Result(0, tag, "")
    return await _docker("build", "-t", tag, str(IMAGE_DIR), timeout=BUILD_TIMEOUT_S)


_starting = asyncio.Lock()


async def ensure_up() -> Result:
    async with _starting:
        if await state() == "running":
            return Result(0, NAME, "")
        if not (built := await ensure_image()).ok:
            return built
        await remove()
        return await _docker(*argv(settings.dir_path / ROOT))


async def rebuild() -> Result:
    await remove()
    result = await ensure_up()
    if not result.ok:
        log.warning("sandbox is not available: %s", result.message)
    return result


async def execute(
    chat_id: str,
    command: str,
    timeout: float = EXEC_TIMEOUT_S,
    on_output: Callable[[str], None] | None = None,
) -> Result:
    argv = exec_argv(chat_id, command, timeout)
    result = await _docker(*argv, timeout=timeout + 10, on_output=on_output)
    if EXEC_START_FAILED not in result.out + result.err:
        return result

    if not (up := await rebuild()).ok:
        return up
    return await _docker(*argv, timeout=timeout + 10, on_output=on_output)


async def remove() -> Result:
    return await _docker("rm", "-f", NAME, timeout=45)
