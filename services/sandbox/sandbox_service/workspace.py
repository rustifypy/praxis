"""One chat's working directory: the only thing a script can see, and the only place a path from
the model is allowed to land.

Moved here from the portal verbatim apart from where the root comes from. The service owns the
workspace now, so every path decision is on this side of the wire — which is what makes
`resolve` the whole guard rather than one of two.
"""

import os
import shutil
from pathlib import Path

ROOT = Path(os.environ.get("SANDBOX_ROOT", "/workspaces"))
LOGS = ".logs"
SCRIPTS = "scripts"
UPLOADS = "uploads"
PROFILES = ".profiles"


def ensure(chat_id: str) -> Path:
    """This chat's workspace, and the mount root above it. Made before `docker run` rather than
    by it: a bind mount whose host path is missing is not an error — docker creates an empty
    directory there, and then the agent's files are somewhere nobody is looking."""
    root = ROOT / chat_id
    (root / LOGS).mkdir(parents=True, exist_ok=True)
    return root


def remove(chat_id: str) -> None:
    shutil.rmtree(ROOT / chat_id, ignore_errors=True)


def resolve(chat_id: str, path: str) -> tuple[Path | None, str]:
    """A model-supplied path inside this chat's workspace, or why not.

    The check is on the *resolved* target rather than the input, which is what makes one check
    cover three escapes: `..`, an absolute path, and a symlink the sandbox planted itself —
    `bash` can point one at `/etc/passwd`, and a host-side read would otherwise follow it.
    """
    root = (ROOT / chat_id).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return None, (
            f"{path!r} is outside the workspace. Every path is relative to /workspace, "
            "and nothing outside it is readable."
        )
    return target, ""


def listing(chat_id: str, pattern: str = "*") -> list[str]:
    """Files matching a glob, relative to the workspace. Dotted paths are skipped, so `.logs`
    does not read as part of the work."""
    root = ROOT / chat_id
    if not root.is_dir():
        return []
    out = []
    for found in root.glob(pattern):
        relative = found.relative_to(root)
        if found.is_file() and not any(part.startswith(".") for part in relative.parts):
            out.append(str(relative))
    return sorted(out)


def snapshot(chat_id: str) -> dict[str, tuple[int, int]]:
    """Every file's `(size, mtime)`, so two snapshots either side of a run say what it wrote.

    Size and mtime rather than names alone: a script that re-draws `chart.png` produces no new
    name, and a set difference would report the second run as having made nothing — which is the
    run whose picture is on screen.
    """
    root = ROOT / chat_id
    out = {}
    for path in listing(chat_id, "**/*"):
        stat = (root / path).stat()
        out[path] = (stat.st_size, stat.st_mtime_ns)
    return out


def save_upload(chat_id: str, filename: str, data: bytes) -> str:
    """Write a file the user handed over and return its path, relative to the workspace.

    The name is a browser's, so it is untrusted twice over: `Path.name` throws away any
    directory part, and `resolve` is what proves the result is still inside this chat's
    workspace. Numbered on collision rather than overwritten — the same reason `save_script`
    numbers: a figure in an earlier answer came from the earlier file, and silently replacing it
    would make that answer unauditable.
    """
    uploads = ensure(chat_id) / UPLOADS
    uploads.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).name.lstrip(".")[-120:] or "upload"
    target, refused = resolve(chat_id, f"{UPLOADS}/{stem}")
    if target is None:
        raise ValueError(refused)
    n = 1
    while target.exists():
        n += 1
        target = uploads / f"{Path(stem).stem}-{n}{Path(stem).suffix}"
    target.write_bytes(data)
    return f"{UPLOADS}/{target.name}"


def uploaded(chat_id: str) -> list[str]:
    """What this chat was given, newest last. `uploads/` is the only record that a file arrived
    from the browser rather than from a script, which is why the directory is the rule."""
    return listing(chat_id, f"{UPLOADS}/*")


def save_script(chat_id: str, script: str) -> str:
    """Write a script into the workspace and return its path, relative to it.

    Saved rather than piped to `python -c`: a computed figure's provenance is then a file on disk
    that outlives the transcript, and the agent can re-read what it actually ran instead of what
    it believes it sent. Numbered, so a second attempt does not overwrite the evidence of the
    first.
    """
    scripts = ensure(chat_id) / SCRIPTS
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / f"run-{len(list(scripts.glob('run-*.py'))) + 1:03d}.py"
    path.write_text(script)
    return f"{SCRIPTS}/{path.name}"


def record(chat_id: str, name: str, text: str, limit: int) -> tuple[str, str]:
    """`(what the model reads, where the whole thing is)`, with the log path empty when nothing
    had to be cut.

    Trimmed in the middle, not at the head: a traceback keeps its cause at one end and its
    message at the other, and a head slice of one is the half that says least. Same split as a
    query result's preview and its `result_id`, except the whole output goes to a file rather
    than a table — an exec's output has no columns and nothing pages through it.
    """
    if len(text) <= limit:
        return text, ""
    logs = ensure(chat_id) / LOGS
    path = logs / f"{name}-{len(list(logs.glob(f'{name}-*.log'))) + 1:03d}.log"
    path.write_text(text)
    half = limit // 2
    trimmed = len(text) - 2 * half
    body = f"{text[:half]}\n\n... {trimmed} characters trimmed ...\n\n{text[-half:]}"
    return body, f"{LOGS}/{path.name}"
