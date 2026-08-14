"""One source's model directory as an editable working tree.

The gate runs before the write because the write is live: Cube watches the mount in dev mode
and reloads in under a second, so a file that will not compile has to be kept off disk rather
than rolled back after. Both callers go through `write` and `delete` — the HTTP routes and the
editor agent's tools — so the two cannot come to disagree about what a valid edit is, or about
whether the directory is versioned at all. Without a repo there is no main to diff against, and
every append-only rule passes vacuously.

A refusal is a value with a `kind`, so HTTP can choose a status and the agent can be told what
to do instead. Neither decision belongs here.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .. import vcs
from ..registry import Source
from . import edits


@dataclass
class Outcome:
    ok: bool
    kind: str = ""
    """Why it was refused: branch | path | missing | rejected | repo. Empty when it was not."""
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    path: str = ""
    """The path actually written, relative to the model directory."""


def files(source: Source) -> dict[str, str]:
    """Every `*.yml` in the working tree, relative path -> text. `.git` holds none."""
    root = source.model_dir()
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.yml"))}


def resolve(source: Source, path: str) -> tuple[Path | None, str]:
    """A caller's path inside the model directory, or why not."""
    root = source.model_dir().resolve()
    # `..` and an absolute path both resolve out of the tree, so the check is on the result.
    target = (root / path).resolve()
    if not target.is_relative_to(root) or ".git" in target.parts:
        return None, "a model file lives inside the source's model directory"
    if target.suffix != ".yml":
        return None, "a model file is a *.yml — Cube ignores the rest of the mount"
    return target, ""


async def editable(source: Source) -> Outcome | None:
    """Why this model must not be changed right now, or None. Versioning it first is part of
    the guard: without a repo there is no main, so `edits.check` would have nothing to diff a
    proposed cube against and every append-only rule would pass vacuously."""
    repo = source.model_dir()
    ensured = await vcs.ensure(repo)
    if not ensured.ok:
        return Outcome(False, "repo", f"could not version {repo}: {ensured.message}")
    if await vcs.current(repo) == vcs.MAIN:
        return Outcome(
            False,
            "branch",
            f"{vcs.MAIN!r} is what the pipeline generates — create a branch before editing",
        )
    return None


async def _editable(source: Source, path: str) -> tuple[Path, str, Outcome | None]:
    """The target, its relative path, and the refusal to return instead. Shared by `write` and
    `delete` so a guard cannot apply to one and not the other."""
    if refusal := await editable(source):
        return Path(), "", refusal
    target, refused = resolve(source, path)
    if target is None:
        return Path(), "", Outcome(False, "path", refused)
    return target, str(target.relative_to(source.model_dir().resolve())), None


async def write(source: Source, path: str, text: str) -> Outcome:
    """Tiers 1-2, then the write."""
    target, relative, refusal = await _editable(source, path)
    if refusal is not None:
        return refusal

    repo = source.model_dir()
    result = edits.check(
        await vcs.files_at(repo, vcs.MAIN, suffix=".yml"), {**files(source), relative: text}
    )
    if not result.ok:
        return Outcome(False, "rejected", result.as_message(), errors=result.errors)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return Outcome(True, path=relative, warnings=result.warnings)


async def delete(source: Source, path: str) -> Outcome:
    """The model minus this file, gated against main before anything leaves disk."""
    target, relative, refusal = await _editable(source, path)
    if refusal is not None:
        return refusal
    if not target.is_file():
        return Outcome(False, "missing", f"no model file at {path!r}")

    repo = source.model_dir()
    remaining = {p: body for p, body in files(source).items() if p != relative}
    result = edits.check(await vcs.files_at(repo, vcs.MAIN, suffix=".yml"), remaining)
    if not result.ok:
        return Outcome(False, "rejected", result.as_message(), errors=result.errors)

    target.unlink()
    return Outcome(True, path=relative)
