"""Version control over the model directory. It is a git repo, so a branch is a git
branch and the checked-out branch is what the container serves.

Beside `modelgen` and `editing` rather than inside either: generation commits what it writes
and editing branches off that, so both need it.

Host-side, not in the container: the model directory is bind-mounted, so these are the exact
bytes Cube reads, `git diff` works in the user's own editor, and `cubejs/cube` carries no git
binary to run this with anyway.
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from ..core import proc
from ..core.proc import Result

MAIN = "main"
"""The generated branch. The pipeline writes it once; every later edit is a branch off it."""
DEFAULT_TIMEOUT_S = 30.0
FIELD = "\x1f"
"""Field separator for `--pretty`. A commit subject can hold a tab or a pipe; not this."""


@dataclass
class Commit:
    sha: str
    author: str
    at: float
    subject: str


async def run(path: Path, *args: str, timeout: float = DEFAULT_TIMEOUT_S) -> Result:
    """One git command in `path`. Never raises for a non-zero exit — callers read `ok`."""
    return await proc.run(
        "git",
        "-C",
        str(path),
        *args,
        timeout=timeout,
        # Nothing here talks to a remote, and a prompt would hang the server rather than
        # fail it. `docker` needs no env, which is why this argument exists at all.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def is_repo(path: Path) -> bool:
    return (path / ".git").is_dir()


async def ensure(path: Path) -> Result:
    """Make `path` a repo with one commit in it, or leave an existing one alone.

    Idempotent, so every caller can ensure a repo without checking for one first — an
    unversioned model directory is a real state, not an error.
    """
    if is_repo(path):
        return Result(0, str(path), "")
    if not path.is_dir():
        return Result(1, "", f"{path} does not exist")

    created = await run(path, "init", "-b", MAIN)
    if not created.ok:
        return created
    # git refuses to commit without an identity, and a machine that has never used git has
    # none. Set one locally only when nothing else resolves, so a user who has configured
    # their own keeps authoring their own commits.
    if not (await run(path, "config", "user.email")).out.strip():
        await run(path, "config", "user.email", "analyst@localhost")
        await run(path, "config", "user.name", "analyst")

    # `--allow-empty` rather than `commit()`: every repo gets a HEAD, because branching,
    # diffing and merging all need one and a scaffold with nothing in it yet is a real state.
    await run(path, "add", "-A")
    return await run(
        path,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-m",
        "initial — model scaffold",
    )


# --- branches ----------------------------------------------------------------


def validate_branch(name: str, taken: list[str] | None = None) -> str | None:
    """Returns an error message, or None when the name is usable."""
    if not name:
        return "a branch name is required"
    if name == MAIN:
        return f"{MAIN!r} is the generated branch — edits go on a branch off it"
    if not name[0].isalnum() or not all(c.isalnum() or c in "-_/" for c in name):
        return "use letters, digits, hyphens and underscores, starting with a letter or digit"
    if name.endswith((".lock", "/")) or ".." in name:
        return "not a valid git branch name"
    if taken and name in taken:
        return f"branch {name!r} already exists"
    return None


async def branches(path: Path) -> list[str]:
    result = await run(path, "branch", "--format=%(refname:short)")
    return [line.strip() for line in result.out.splitlines() if line.strip()] if result.ok else []


async def current(path: Path) -> str:
    """The checked-out branch — which is the model the container is serving."""
    return (await run(path, "branch", "--show-current")).out.strip()


async def create(path: Path, name: str) -> Result:
    """Branch off the checked-out commit and switch to it."""
    return await run(path, "checkout", "-b", name)


async def checkout(path: Path, name: str) -> Result:
    """Switch branches. **The caller must restart the container and drop the cached catalog** —
    a whole branch changing at once is not what the file watcher is good at, and the agent's
    cached catalog is not watched at all.
    """
    return await run(path, "checkout", name)


async def delete(path: Path, name: str, force: bool = False) -> Result:
    return await run(path, "branch", "-D" if force else "-d", name)


# --- reading -----------------------------------------------------------------


async def status(path: Path) -> list[str]:
    """Paths changed in the working tree — someone editing YAML in their own editor."""
    result = await run(path, "status", "--porcelain")
    return [line[3:].strip() for line in result.out.splitlines() if line.strip()]


async def changed(path: Path, base: str, ref: str) -> list[tuple[str, str]]:
    """(status letter, path) for what `ref` changed since it left `base`."""
    result = await run(path, "diff", "--name-status", f"{base}...{ref}")
    out = []
    for line in result.out.splitlines():
        code, _, name = line.partition("\t")
        if name.strip():
            out.append((code.strip(), name.strip()))
    return out


async def files_at(path: Path, ref: str, suffix: str = "") -> dict[str, str]:
    """Every tracked file at `ref`, as relative path -> text. The edit gate diffs a proposed
    model against main's, and reading main means reading it out of git rather than off disk —
    disk is whichever branch happens to be checked out."""
    listed = await run(path, "ls-tree", "-r", "--name-only", ref)
    if not listed.ok:
        return {}
    names = [n for n in listed.out.splitlines() if n.strip() and n.endswith(suffix)]
    bodies = await asyncio.gather(*(run(path, "show", f"{ref}:{name}") for name in names))
    return {name: body.out for name, body in zip(names, bodies, strict=True) if body.ok}


async def diff(path: Path, base: str, ref: str = "") -> str:
    """The patch a merge would apply. Three dots: what `ref` did, not what `base` did since."""
    spec = f"{base}...{ref}" if ref else base
    return (await run(path, "diff", spec)).out


async def log(path: Path, ref: str = "", limit: int = 50) -> list[Commit]:
    result = await run(
        path,
        "log",
        f"--max-count={limit}",
        f"--pretty=format:%H{FIELD}%an{FIELD}%at{FIELD}%s",
        *([ref] if ref else []),
    )
    out = []
    for line in result.out.splitlines():
        parts = line.split(FIELD)
        if len(parts) == 4:
            out.append(Commit(parts[0], parts[1], float(parts[2]), parts[3]))
    return out


# --- writing -----------------------------------------------------------------


async def commit(path: Path, message: str) -> Result:
    """Stage everything and commit. A clean tree is success, so callers commit freely rather
    than checking for changes first."""
    staged = await run(path, "add", "-A")
    if not staged.ok:
        return staged
    if not (await run(path, "status", "--porcelain")).out.strip():
        return Result(0, "", "")
    # gpgsign off: a global signing config fails every commit on a machine whose key is not
    # loaded, and nothing here is published.
    return await run(path, "-c", "commit.gpgsign=false", "commit", "-m", message)


async def discard(path: Path) -> Result:
    """Throw uncommitted work away — tracked files restored, untracked ones removed.

    A branch cannot be switched while the tree is dirty, so the alternative to offering this is
    committing edits nobody wants in order to escape them.
    """
    restored = await run(path, "checkout", "--", ".")
    if not restored.ok:
        return restored
    return await run(path, "clean", "-fd")


async def merge(path: Path, branch: str, message: str = "") -> Result:
    """Merge `branch` into whatever is checked out, or leave the tree untouched.

    A conflict is aborted immediately rather than reported and left: git writes conflict
    markers into the working tree, that tree is the live bind mount, and a `<<<<<<<` inside a
    cube file takes the model to **zero cubes** the moment Cube reloads — the same failure a
    stray `grounding/probe.yml` causes, from a direction nobody would look for it.
    """
    result = await run(
        path,
        "-c",
        "commit.gpgsign=false",
        "merge",
        "--no-ff",
        branch,
        "-m",
        message or f"merge {branch}",
    )
    if not result.ok:
        # Fails harmlessly when no merge started (an unknown branch), which is why its
        # result is not checked — `result` is the answer either way.
        await run(path, "merge", "--abort")
    return result
