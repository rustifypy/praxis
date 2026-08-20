"""Running one script, and stopping it.

The service *is* the sandbox, so a script is a subprocess rather than a `docker exec` into a
sibling container. Two things fall out of that and both are simplifications: the pid is an object
this process holds, so stopping a run needs no pid file and no `kill` inside a container with no
`ps`; and there is no exec client to outlive the command it started.

The limits that used to be `docker run` flags are the container's own, from the compose file —
`mem_limit`, `pids_limit`, `read_only`. Shared across chats, as they were before: a script
exhausting either affects every chat, which is survivable because the workspace is the state.
"""

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import AsyncIterator
from pathlib import Path

from . import workspace

EXEC_TIMEOUT_S = 120.0
OUTPUT_LIMIT = 4_000
"""What the caller is handed. The whole thing goes to a log file in the workspace, so this is a
head-and-tail rather than a truncation — a traceback keeps its cause at one end and its message
at the other."""

CHUNK = 8_192
"""`StreamReader.readline` raises past 64 KiB, and one `print` of a wide dataframe has no newline
in it — so output is read in chunks rather than lines."""

KILL_GRACE_S = 5.0
"""How long a TERMed run has to exit before it is KILLed."""

FIXED_ENV = {
    "HOME": "/tmp",
    "MPLCONFIGDIR": "/tmp/mpl",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "MPLBACKEND": "Agg",
}

_running: dict[str, asyncio.subprocess.Process] = {}
"""chat id -> its process. One run per chat: a second would race on the same workspace."""


def busy(chat_id: str) -> bool:
    return chat_id in _running


def _signal(process: asyncio.subprocess.Process, sig: int) -> None:
    """Signal the whole process group. A script that spawned children leaves them running if
    only the interpreter is signalled, and they keep holding the workspace."""
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(sig)


async def kill(chat_id: str) -> bool:
    """Stop the run, if there is one. TERM, then KILL if it does not go."""
    process = _running.get(chat_id)
    if process is None or process.returncode is not None:
        return False
    _signal(process, signal.SIGTERM)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), KILL_GRACE_S)
        return True
    _signal(process, signal.SIGKILL)
    return True


async def run(chat_id: str, script: str, timeout: float = EXEC_TIMEOUT_S) -> AsyncIterator[dict]:
    """Save the script, run it, stream what it prints, then yield exactly one result.

    The script is written to the workspace first and run from there rather than piped to
    `python -c`: a computed figure's provenance is then a file that outlives the transcript, and
    a second attempt does not overwrite the evidence of the first.
    """
    root = workspace.ensure(chat_id)
    path = workspace.save_script(chat_id, script)
    before = workspace.snapshot(chat_id)

    process = await asyncio.create_subprocess_exec(
        "python",
        path,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **FIXED_ENV},
        # Its own process group, so one signal reaches whatever the script spawned. This is also
        # why the timeout is enforced here rather than by a `timeout` wrapper: the process is an
        # object we hold, so there is nothing left to reach around.
        start_new_session=True,
    )
    _running[chat_id] = process

    out: list[str] = []
    err: list[str] = []
    expired = False

    async def collect(stream, into: list[str]) -> None:
        while chunk := await stream.read(CHUNK):
            into.append(chunk.decode(errors="replace"))

    # stderr is collected rather than streamed, because a traceback interleaved into the output
    # reads as part of it. stdout is drained by the loop below, which is what streams.
    errors = asyncio.create_task(collect(process.stderr, err))
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            left = deadline - asyncio.get_running_loop().time()
            if left <= 0:
                expired = True
                break
            try:
                chunk = await asyncio.wait_for(process.stdout.read(CHUNK), left)
            except TimeoutError:
                expired = True
                break
            if not chunk:
                break
            text = chunk.decode(errors="replace")
            out.append(text)
            yield {"output": text}

        if expired:
            await kill(chat_id)
        await process.wait()
        await errors
    finally:
        errors.cancel()
        _running.pop(chat_id, None)

    whole_out, out_log = workspace.record(chat_id, "out", "".join(out), OUTPUT_LIMIT)
    whole_err, err_log = workspace.record(chat_id, "err", "".join(err), OUTPUT_LIMIT)
    after = workspace.snapshot(chat_id)

    yield {
        "result": {
            "code": process.returncode or 0,
            "expired": expired,
            "out": whole_out,
            "err": whole_err,
            "out_log": out_log,
            "err_log": err_log,
            "script": path,
            # Keyed on (size, mtime), not names: a script that re-draws `chart.png` adds no
            # name, and the run whose picture is on screen would report writing nothing.
            "wrote": sorted(p for p, stamp in after.items() if before.get(p) != stamp),
        }
    }


async def profile(chat_id: str, path: str, timeout: float = 60.0) -> str:
    """Run the profiler over one file and return its last line of stdout, which is the JSON.

    Copied in from beside this module rather than baked into the image: the image is tagged by
    its Dockerfile's digest, so a profiler shipped inside it would be served stale after every
    edit to it.
    """
    source = Path(__file__).parent / "profiler.py"
    target = workspace.ensure(chat_id) / workspace.PROFILES / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())

    process = await asyncio.create_subprocess_exec(
        "python",
        f"{workspace.PROFILES}/{source.name}",
        path,
        cwd=str(workspace.ensure(chat_id)),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **FIXED_ENV},
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        _signal(process, signal.SIGKILL)
        raise RuntimeError(f"the profiler did not finish within {timeout:.0f}s") from None
    printed = stdout.decode(errors="replace").strip()
    if not printed:
        raise RuntimeError(f"exit {process.returncode}: {stderr.decode(errors='replace')[:200]}")
    return printed.splitlines()[-1]


def cache_path(chat_id: str, path: str) -> Path:
    return workspace.ensure(chat_id) / workspace.PROFILES / f"{Path(path).name}.json"


def keep(chat_id: str, path: str, profile: dict) -> None:
    target = cache_path(chat_id, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile))


def cached(chat_id: str) -> list[dict]:
    """Every upload this chat holds, with the shape read on arrival. The listing is the
    authority and the cache is only the shape: a file whose profile is missing is still named,
    because the agent can open it either way."""
    out = []
    for path in workspace.uploaded(chat_id):
        try:
            out.append(json.loads(cache_path(chat_id, path).read_text()))
        except (OSError, ValueError):
            out.append({"path": path, "note": "not profiled"})
    return out


def forget(chat_id: str, path: str) -> None:
    cache_path(chat_id, path).unlink(missing_ok=True)
