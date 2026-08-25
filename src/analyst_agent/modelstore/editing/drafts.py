"""A ref's model files while they are edited: `<DATA_DIR>/drafts/<ref>/{origin,model}/` and
`origin.json`. The store owns every commit; this is a working copy, and there is no git here."""

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ...core.config import settings

MARKER = "origin.json"


@dataclass(frozen=True)
class Origin:
    ref: str
    rev: int
    sha: str
    synced_at: float


@dataclass
class Changes:
    write: dict[str, str] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.write or self.delete)


def _dir(ref: str) -> Path:
    # A ref becomes a directory name, so one with a slash would nest a draft inside another.
    if not ref or "/" in ref or "\\" in ref or ref.startswith(".") or ".." in ref:
        raise ValueError(f"{ref!r} cannot name a draft directory")
    return settings.dir_path / "drafts" / ref


def _resolve(root: Path, path: str) -> Path:
    # Refused rather than stripped: dropping a leading `/` turns `/etc/passwd.yml` into a
    # plausible relative path that would land inside the model.
    parts = path.strip().split("/")
    if not all(parts) or ".." in parts or any(part.startswith(".") for part in parts):
        raise ValueError(f"{path!r} is not a path inside the model")
    target = (root / path.strip()).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"{path!r} leaves the model")
    return target


def _tree(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts):
            found[relative.as_posix()] = path.read_text(encoding="utf-8")
    return found


def _write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)


def _replace(root: Path, content: dict[str, str]) -> None:
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    for path, text in content.items():
        _write(_resolve(root, path), text)


def exists(ref: str) -> bool:
    return (_dir(ref) / MARKER).is_file()


def origin_at(ref: str) -> Origin | None:
    try:
        stored = json.loads((_dir(ref) / MARKER).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return Origin(
        str(stored["ref"]),
        int(stored["rev"]),
        str(stored.get("sha") or ""),
        float(stored.get("synced_at") or 0.0),
    )


def sync(ref: str, content: dict[str, str], rev: int, sha: str = "") -> Origin:
    # Checkout and re-checkout, so it discards local edits. A store that moved under a dirty
    # draft is the staleness case and is not this.
    root = _dir(ref)
    _replace(root / "origin", content)
    _replace(root / "model", content)
    stamped = Origin(ref, rev, sha, time.time())
    _write(root / MARKER, json.dumps(stamped.__dict__, indent=2) + "\n")
    return stamped


def files(ref: str) -> dict[str, str]:
    return _tree(_dir(ref) / "model")


def read(ref: str, path: str) -> str | None:
    try:
        return _resolve(_dir(ref) / "model", path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def save(ref: str, path: str, text: str) -> None:
    _write(_resolve(_dir(ref) / "model", path), text)


def remove(ref: str, path: str) -> bool:
    target = _resolve(_dir(ref) / "model", path)
    if not target.is_file():
        return False
    target.unlink()
    return True


def discard(ref: str, path: str | None = None) -> None:
    root = _dir(ref)
    if path is None:
        _replace(root / "model", _tree(root / "origin"))
        return
    base, target = _resolve(root / "origin", path), _resolve(root / "model", path)
    # Deleted rather than emptied, so discarding a file the store never had leaves no zero-byte
    # YAML behind for Cube to compile.
    if base.is_file():
        _write(target, base.read_text(encoding="utf-8"))
    elif target.is_file():
        target.unlink()


def changes(ref: str) -> Changes:
    root = _dir(ref)
    current, base = _tree(root / "model"), _tree(root / "origin")
    return Changes(
        write={path: text for path, text in current.items() if base.get(path) != text},
        delete=[path for path in base if path not in current],
    )


def drop(ref: str) -> None:
    shutil.rmtree(_dir(ref), ignore_errors=True)
