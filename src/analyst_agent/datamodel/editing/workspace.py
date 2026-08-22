from dataclasses import dataclass, field

from ...core.deployment import MAIN
from ...modelstore.client import ModelStore, StoreError
from . import edits


@dataclass
class Outcome:
    ok: bool
    # Why it was refused: ref | path | missing | rejected | store. Empty when it was not.
    kind: str = ""
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    path: str = ""


def resolve(path: str) -> tuple[str, str]:
    """The path a model file has inside a ref, or ('', reason)."""
    cleaned = path.strip()
    parts = cleaned.split("/")
    # Relative and inside the model: the store resolves a path against the ref's own root, so an
    # absolute one is not a near-miss — it is a different tree.
    if (
        not cleaned
        or cleaned.startswith("/")
        or ".." in parts
        or any(part.startswith(".") for part in parts)
    ):
        return "", "a model file lives inside the model, so its path cannot leave it"
    if not cleaned.endswith(".yml"):
        return "", "a model file is a *.yml — Cube ignores everything else in the model"
    return cleaned, ""


def validate_ref(name: str, taken: list[str] | None = None) -> str | None:
    """Returns an error message, or None when the name is usable as a ref."""
    if not name:
        return "a name is required"
    if name == MAIN:
        return f"{MAIN!r} is the generated model — edits go on a ref off it"
    if not name[0].isalnum() or not all(c.isalnum() or c in "-_/" for c in name):
        return "use letters, digits, hyphens and underscores, starting with a letter or digit"
    if name.endswith((".lock", "/")) or ".." in name:
        return "not a valid ref name"
    if taken and name in taken:
        return f"{name!r} already exists"
    return None


def editable(ref: str) -> Outcome | None:
    if ref == MAIN:
        return Outcome(
            False, "ref", f"{MAIN!r} is what the pipeline generates — edit a ref off it instead"
        )
    return None


def model_yaml(files: dict[str, str]) -> dict[str, str]:
    # A ref carries its grounding markdown as well as its model, and the gate reads definitions.
    return {name: body for name, body in files.items() if name.endswith(".yml")}


async def write(store: ModelStore, ref: str, path: str, text: str) -> Outcome:
    # Tiers 1-2, then the write. The gate runs first because the write *is* the deploy: Cube
    # polls the store for the ref's version, so a commit is live within seconds and there is no
    # restart afterwards to hide behind.
    try:
        if refusal := editable(ref):
            return refusal
        relative, refused = resolve(path)
        if not relative:
            return Outcome(False, "path", refused)
        proposed = {**model_yaml(await store.files(ref, refresh=True)), relative: text}
        result = edits.check(model_yaml(await store.files(MAIN)), proposed)
        if not result.ok:
            return Outcome(False, "rejected", result.as_message(), errors=result.errors)
        await store.edit(ref, write={relative: text}, message=f"edit {relative}")
        return Outcome(True, path=relative, warnings=result.warnings)
    except StoreError as exc:
        return Outcome(False, "store", str(exc))


async def delete(store: ModelStore, ref: str, path: str) -> Outcome:
    try:
        if refusal := editable(ref):
            return refusal
        relative, refused = resolve(path)
        if not relative:
            return Outcome(False, "path", refused)
        current = model_yaml(await store.files(ref, refresh=True))
        if relative not in current:
            return Outcome(False, "missing", f"no model file at {path!r}")
        remaining = {name: body for name, body in current.items() if name != relative}
        result = edits.check(model_yaml(await store.files(MAIN)), remaining)
        if not result.ok:
            return Outcome(False, "rejected", result.as_message(), errors=result.errors)
        await store.edit(ref, delete=[relative], message=f"delete {relative}")
        return Outcome(True, path=relative)
    except StoreError as exc:
        return Outcome(False, "store", str(exc))
