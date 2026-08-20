import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

MAIN = "main"
TIMEOUT_S = 30.0
ALLOWED = (("cubes/", ".yml"), ("views/", ".yml"), ("grounding/", ".md"))
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class RepoError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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
        return (self.err or self.out).strip()[:800]


@dataclass
class Commit:
    sha: str
    author: str
    at: float
    subject: str


async def git(
    root: Path, *args: str, stdin: bytes | None = None, index: str | None = None
) -> Result:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if index:
        env["GIT_INDEX_FILE"] = index
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "--git-dir",
            str(root),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return Result(127, "", "git not found on PATH")
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return Result(124, "", f"git {args[0]} did not finish within {TIMEOUT_S:.0f}s")
    return Result(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))


async def must(root: Path, *args: str, stdin: bytes | None = None, index: str | None = None) -> str:
    result = await git(root, *args, stdin=stdin, index=index)
    if not result.ok:
        raise RepoError(f"git {args[0]}: {result.message}", 500)
    return result.out


def rejected(path: str, body: str) -> str:
    if not path or path.startswith("/") or ".." in path.split("/") or path != path.strip():
        return f"{path!r} is not a relative path inside the model"
    if not any(path.startswith(p) and path.endswith(s) for p, s in ALLOWED):
        return f"{path!r} must be cubes/*.yml, views/*.yml or grounding/**.md"
    if path.endswith(".yml"):
        try:
            yaml.safe_load(body)
        except yaml.YAMLError as exc:
            return f"{path}: {exc}"
    return ""


def check(files: dict[str, str]) -> list[str]:
    return [reason for path, body in files.items() if (reason := rejected(path, body))]


async def ensure(root: Path) -> None:
    if (root / "HEAD").exists():
        return
    root.mkdir(parents=True, exist_ok=True)
    await must(root, "init", "--bare", "-b", MAIN)
    await must(root, "config", "user.email", "store@localhost")
    await must(root, "config", "user.name", "model-store")
    await must(root, "hash-object", "-w", "-t", "tree", "--stdin", stdin=b"")
    sha = (await must(root, "commit-tree", EMPTY_TREE, "-m", "initial")).strip()
    await must(root, "update-ref", f"refs/heads/{MAIN}", sha)


async def refs(root: Path) -> list[dict]:
    out = await must(root, "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads")
    return [
        {"name": name, "sha": sha}
        for name, _, sha in (line.partition("\t") for line in out.splitlines())
        if name
    ]


async def version(root: Path, ref: str) -> str:
    result = await git(root, "rev-parse", "--verify", f"refs/heads/{ref}")
    if not result.ok:
        raise RepoError(f"no such ref: {ref!r}", 404)
    return result.out.strip()


async def files(root: Path, ref: str) -> dict[str, str]:
    await version(root, ref)
    listed = await must(root, "ls-tree", "-r", "--name-only", ref)
    names = [name for name in listed.splitlines() if name.strip()]
    bodies = await asyncio.gather(*(git(root, "cat-file", "blob", f"{ref}:{n}") for n in names))
    return {n: b.out for n, b in zip(names, bodies, strict=True) if b.ok}


async def one_file(root: Path, ref: str, path: str) -> str:
    result = await git(root, "cat-file", "blob", f"{ref}:{path}")
    if not result.ok:
        raise RepoError(f"no {path!r} at {ref!r}", 404)
    return result.out


async def create_ref(root: Path, name: str, from_ref: str) -> str:
    if name == MAIN:
        raise RepoError(f"{MAIN!r} already exists")
    if invalid := bad_ref_name(name):
        raise RepoError(invalid)
    sha = await version(root, from_ref)
    # An empty old-value means "must not exist" — the create is refused rather than moved.
    result = await git(root, "update-ref", f"refs/heads/{name}", sha, "")
    if not result.ok:
        raise RepoError(f"ref {name!r} already exists")
    return sha


async def delete_ref(root: Path, name: str) -> None:
    if name == MAIN:
        raise RepoError(f"{MAIN!r} cannot be deleted")
    await version(root, name)
    await must(root, "update-ref", "-d", f"refs/heads/{name}")


def bad_ref_name(name: str) -> str:
    if not name:
        return "a ref name is required"
    if not name[0].isalnum() or not all(c.isalnum() or c in "-_/" for c in name):
        return "use letters, digits, hyphens and underscores, starting with a letter or digit"
    if name.endswith((".lock", "/")) or ".." in name:
        return f"{name!r} is not a valid git ref name"
    return ""


async def _commit(root: Path, ref: str, parent: str, tree: str, message: str) -> str:
    if tree == (await must(root, "rev-parse", f"{parent}^{{tree}}")).strip():
        return parent
    sha = (await must(root, "commit-tree", tree, "-p", parent, "-m", message)).strip()
    # Compare-and-swap on the old sha: a concurrent write to the same ref loses rather than
    # silently discarding the winner's commit.
    result = await git(root, "update-ref", f"refs/heads/{ref}", sha, parent)
    if not result.ok:
        raise RepoError(f"{ref!r} moved while writing — retry", 409)
    return sha


async def commit_files(
    root: Path,
    ref: str,
    write: dict[str, str],
    remove: list[str],
    message: str,
    replace: bool,
) -> str:
    if problems := check(write):
        raise RepoError("; ".join(problems))
    parent = await version(root, ref)

    with tempfile.TemporaryDirectory() as scratch:
        index = str(Path(scratch) / "index")
        # A fresh index means the written set *is* the tree; seeding it from the ref keeps
        # everything else, which is the only difference between a rebuild and an edit.
        if not replace:
            await must(root, "read-tree", ref, index=index)
        lines = []
        for path, body in write.items():
            blob = await must(
                root, "hash-object", "-w", "--stdin", stdin=body.encode(), index=index
            )
            lines.append(f"100644 {blob.strip()}\t{path}")
        # Mode 0 with the null sha removes an entry.
        lines += [f"0 {'0' * 40}\t{path}" for path in remove]
        if lines:
            await must(
                root,
                "update-index",
                "--index-info",
                stdin="\n".join(lines).encode() + b"\n",
                index=index,
            )
        tree = (await must(root, "write-tree", index=index)).strip()

    return await _commit(root, ref, parent, tree, message)


async def diff(root: Path, base: str, ref: str) -> dict:
    await version(root, base)
    await version(root, ref)
    spec = f"{base}...{ref}"
    # --no-renames so a rename reads as delete-plus-add. Impact analysis asks "is what this
    # tile reads still here", and a rename is exactly that breakage; R100 would hide it.
    names = await must(root, "diff", "--no-renames", "--name-status", spec)
    changed = []
    for line in names.splitlines():
        code, _, name = line.partition("\t")
        if name.strip():
            changed.append({"status": code.strip(), "path": name.strip()})
    return {"changed": changed, "patch": await must(root, "diff", spec)}


async def merge(root: Path, from_ref: str, into: str, message: str) -> str:
    parent = await version(root, into)
    head = await version(root, from_ref)
    # merge-tree writes the merged tree without a working tree, so a conflict cannot leave
    # markers in anything Cube would compile. Exit 1 is conflicts, not a failure to run.
    result = await git(root, "merge-tree", "--write-tree", into, from_ref)
    if result.code == 1:
        raise RepoError(f"merge conflict:\n{result.out.strip()[:800]}", 409)
    if not result.ok:
        raise RepoError(f"git merge-tree: {result.message}", 500)

    tree = result.out.splitlines()[0].strip()
    if tree == (await must(root, "rev-parse", f"{parent}^{{tree}}")).strip():
        return parent
    sha = (await must(root, "commit-tree", tree, "-p", parent, "-p", head, "-m", message)).strip()
    updated = await git(root, "update-ref", f"refs/heads/{into}", sha, parent)
    if not updated.ok:
        raise RepoError(f"{into!r} moved while merging — retry", 409)
    return sha


async def log(root: Path, ref: str, limit: int) -> list[Commit]:
    await version(root, ref)
    out = await must(
        root, "log", f"--max-count={limit}", "--pretty=format:%H\x1f%an\x1f%at\x1f%s", ref
    )
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append(Commit(parts[0], parts[1], float(parts[2]), parts[3]))
    return commits
