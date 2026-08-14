"""One external command, as a value rather than an exception.

`docker` and `git` are the two programs this runs, and both need the same three things:
**never raise on a non-zero exit** — a container that will not start and a branch that will
not check out are UI states, not 500s — a timeout that kills rather than hanging the event
loop, and whichever stream said something as one string a page can show.

How a subprocess is run is not a per-program decision, so it is decided once here.
"""

import asyncio
from dataclasses import dataclass

DEFAULT_TIMEOUT_S = 60.0


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def message(self) -> str:
        """Whichever stream said something, trimmed for a UI."""
        return (self.err or self.out).strip()[:800]


async def run(
    program: str,
    *args: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    env: dict[str, str] | None = None,
) -> Result:
    """Run `program` with `args`. 127 is "not installed", 124 is "timed out" — the two
    failures that are about the machine rather than about what was asked.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            program,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return Result(127, "", f"{program} not found on PATH")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Result(124, "", f"{program} did not finish within {timeout:.0f}s")
    return Result(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))
