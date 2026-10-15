"""The one door every change to a model file goes through.

Both the HTTP routes in `web/model.py` and the editor agent's tools come through here, so the two
cannot disagree about what a valid edit is. Three entry points, deliberately not
interchangeable:

- `stage` is tiers 1-2 — valid YAML, cubes append-only against **main**, views shaped the way
  Cube demands — and it writes nothing anywhere.
- `verify` is tiers 1-3 plus the impact on saved queries, and **it publishes nothing**. It exists
  because Cube only has an opinion about files that are in the store: the draft is written to a
  throwaway ref, compiled there, and that ref is deleted. This is what the editor's Save runs.
- `publish` is tiers 1-2 again and then one commit at the store. It *is* the deploy — Cube asks
  the store for the ref's revision on every request, so what lands here is served within seconds.

**The draft is the current model.** A ref being edited has a working copy on this machine
(`drafts`), and everything here reads it rather than the ref as committed. `write`/`delete` on
`stage` and `verify` are an *extra* overlay on top of it — the editor agent gating a proposal it
has not written anywhere. A publish carries the draft's own change set and no overlay.

**Everything here is set-shaped.** A change that spans two files (a new cube and the view that
includes it) cannot compile one file at a time, so the unit is the whole change set and a
verdict covers all of it or none of it.

A failure is an `Outcome` or a `Verdict`, never an exception. Both callers turn one into a status
code or into prose for the model, so a `StoreError` escaping this module would 500 a route and
kill a turn.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field

from ...core.deployment import MAIN
from ..client import ModelStore, StoreError, served_catalog
from ..modelgen.publish import check as compiles
from . import drafts, edits, impact

# What a ref may hold, mirroring the store's own `ALLOWED` rather than trusting it. Refusing here
# means the reason is a sentence someone can act on instead of a 400 relayed from the store —
# and the editor needs the same rule client-side to know which files it may offer.
ALLOWED = (("cubes/", ".yml"), ("views/", ".yml"))

# The throwaway ref `verify` compiles on. One name rather than one per call: at most one stale
# copy can ever be left behind, and it is deleted on the way in as well as on the way out.
PREVIEW = "preview"

# One name means two concurrent Saves would race on it — the second create would fail, or worse,
# compile the first one's files. Serialised rather than made unique because a person saves one
# thing at a time and a queue of two is invisible at this timescale.
_previewing = asyncio.Lock()


@dataclass
class Outcome:
    """Whether a change set may proceed, and what its paths resolved to.

    `kind` is why it was refused — `ref | path | missing | rejected | store` — and is what the
    routes turn into a status code. It is empty when nothing was refused.

    `write` and `delete` carry the change set with every path already resolved, so a caller that
    has asked `stage` never resolves a second time and cannot resolve differently when it does.
    """

    ok: bool
    kind: str = ""
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    write: dict[str, str] = field(default_factory=dict)
    delete: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    """What `verify` learned about a change set that has not been committed anywhere.

    Its own shape rather than more fields on `Outcome`, because `compiled`, `compile_error`,
    `member_errors` and `broken` are answers only Cube can give, and every caller of `stage` and
    `commit` would otherwise carry four fields it can never set.

    `ok` is tiers 1-2 passing *and* Cube compiling it. `member_errors` and `broken` are
    deliberately not part of it: a model that compiles with a measure that fails at query time
    still beats one that will not compile, and a tile this change would break is the merge's
    business to refuse, not the editor's.
    """

    ok: bool
    kind: str = ""
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    compiled: bool = False
    compile_error: str = ""
    member_errors: dict[str, str] = field(default_factory=dict)
    # None when nothing compiled, so there was no catalog to check saved queries against.
    broken: impact.Impact | None = None


def resolve(path: str) -> tuple[str, str]:
    """The path a model file has inside a ref, or `('', reason)`.

    Shape only. A bare repo has no working tree, so there is nothing on this machine to resolve
    against and nothing to compare a resolved path with — the store resolves whatever it is given
    against the ref's own root. An absolute path, a `..` segment and a dotted segment are
    therefore refused outright rather than stripped: stripping a leading `/` turns
    `/etc/passwd.yml` into a plausible relative path that the store would happily place inside
    the ref.

    It does not say whether the file exists. `stage` learns that from the store, and only for a
    delete, where absence is the thing worth reporting.
    """
    cleaned = path.strip()
    parts = cleaned.split("/")
    if (
        not cleaned
        or cleaned.startswith("/")
        or ".." in parts
        or any(part.startswith(".") for part in parts)
    ):
        return "", "a model file lives inside the model, so its path cannot leave it"

    if not any(cleaned.startswith(head) and cleaned.endswith(tail) for head, tail in ALLOWED):
        # Cube compiles every *.yml anywhere under the model mount, not just cubes/ and views/,
        # so a stray one elsewhere is not inert — verified live, a single misplaced file took a
        # 13-cube model to zero with `Unexpected YAML key: name`.
        return "", (
            "a model file is cubes/*.yml or views/*.yml — Cube ignores anything else in the "
            "model, so writing it would be a silent no-op"
        )
    return cleaned, ""


def validate_ref(name: str, taken: list[str] | None = None) -> str | None:
    """An error message, or None when the name is usable as a ref.

    The rules are git's, restated here so the message arrives before the request rather than as a
    relayed refusal from the store.
    """
    if not name:
        return "a name is required"
    if name == MAIN:
        return f"{MAIN!r} is the generated model — edits go on a ref off it"
    if name == PREVIEW:
        # `verify` creates and deletes this ref underneath whatever is being edited, so a branch
        # holding the same name would be silently destroyed by the next Save.
        return f"{PREVIEW!r} is used to compile an edit before it is committed"
    # No slash: the store cannot disambiguate `/refs/{ref}/files/{path}` when a ref may contain
    # one, and a draft directory named for it would nest one draft inside another.
    if not name[0].isalnum() or not all(c.isalnum() or c in "-_." for c in name):
        return "use letters, digits, hyphens and underscores, starting with a letter or digit"
    if name.endswith(".lock") or ".." in name:
        return "not a valid ref name"
    if taken and name in taken:
        return f"{name!r} already exists"
    return None


def editable(ref: str) -> Outcome | None:
    """None when this ref may be written to, an `Outcome` saying why not otherwise.

    One rule in one place, because the routes and the agent's tools both ask it and only one of
    them can be authoritative. Main is generated: re-running the pipeline rewrites every file in
    it, so an edit made there is work waiting to be silently discarded.
    """
    if ref == MAIN:
        return Outcome(
            False, "ref", f"{MAIN!r} is what the pipeline generates — edit a ref off it instead"
        )
    return None


def model_yaml(files: dict[str, str]) -> dict[str, str]:
    """Just the YAML, for the two things that only understand YAML.

    `edits.check` reads cube and view definitions and Cube compiles the same set, so both are
    asked about this subset — anything else on a ref has no definitions to freeze and nothing
    to compile.
    """
    return {name: body for name, body in files.items() if name.endswith(".yml")}


async def ensure(store: ModelStore, ref: str) -> None:
    """Check the ref out, if what is on this machine is not this ref as the store holds it.

    Here rather than in the routes so every entry point is self-healing: the agent's tools and
    the HTTP surface both reach a ref through this module, and a missing draft is a ref nobody
    has opened yet rather than an error.

    **A clean draft re-checks out whenever the store's revision has moved.** There is nothing to
    lose by doing it — a draft with no changes is a copy, not work — and it is what keeps a ref
    that moved underneath one from being read through a stale copy. The case that made this
    necessary is a name reused: `delete` then `create` gives a *different* ref with the same
    name, and a draft left behind from the first one reported itself clean against an origin that
    no longer existed, so the editor listed files the ref did not have and Publish did nothing.

    A **dirty** draft is left alone even when the store has moved. That divergence is the
    staleness `origin` exists to make comparable, and it is reported rather than resolved:
    re-checking out here would silently discard someone's edit.
    """
    if not drafts.exists(ref):
        drafts.sync(ref, await store.files(ref, refresh=True), await store.rev(ref))
        return
    if drafts.changes(ref).dirty:
        return
    origin, rev = drafts.origin_at(ref), await store.rev(ref)
    if origin is None or origin.rev != rev:
        drafts.sync(ref, await store.files(ref, refresh=True), rev)


async def forget(store: ModelStore, ref: str) -> None:
    """Delete the ref, and the draft that was a copy of it.

    One function because the two must not come apart: the store dropping a ref while its working
    copy stayed behind is what let a reused name resurrect the old one.
    """
    await store.delete_ref(ref)
    drafts.drop(ref)


async def stage(
    store: ModelStore,
    ref: str,
    write: dict[str, str] | None = None,
    delete: list[str] | None = None,
) -> Outcome:
    """Tiers 1-2 over the draft plus an overlay, writing nothing.

    Tier 1 is the path and the YAML; tier 2 is `edits.check` — cubes append-only, views free to
    be rewritten — always **against main** and never against this ref's own previous state. A
    member main carries is frozen because a dashboard tile may name it, and
    diffing against the ref's last commit would unfreeze anything the ref had already changed.

    It refuses a delete of a file that is not there, which is the one existence question worth
    asking: a write to a path that does not exist yet is how every new view starts.
    """
    try:
        if refusal := editable(ref):
            return refusal
        await ensure(store, ref)

        resolved: dict[str, str] = {}
        removing: list[str] = []
        for path, text in (write or {}).items():
            relative, refused = resolve(path)
            if not relative:
                return Outcome(False, "path", refused)
            resolved[relative] = text
        for path in delete or []:
            relative, refused = resolve(path)
            if not relative:
                return Outcome(False, "path", refused)
            removing.append(relative)

        current = drafts.files(ref)
        if absent := [path for path in removing if path not in current]:
            return Outcome(False, "missing", f"no model file at {', '.join(absent)}")

        # The whole model as it would be, not the diff: `edits.check` compares two complete
        # models, which is how a view whose join_path lost its cube is caught in the same pass
        # as the cube that went missing.
        proposed = {
            **model_yaml(current),
            **{path: text for path, text in resolved.items() if path.endswith(".yml")},
        }
        for path in removing:
            proposed.pop(path, None)

        result = edits.check(model_yaml(await store.files(MAIN)), proposed)
        if not result.ok:
            return Outcome(False, "rejected", result.as_message(), errors=result.errors)
        return Outcome(True, warnings=result.warnings, write=resolved, delete=removing)
    except StoreError as exc:
        # Not raised: both callers turn a refusal into a status code or into prose, and an
        # exception escaping here would 500 the route or kill the turn.
        return Outcome(False, "store", str(exc))


async def verify(
    store: ModelStore,
    ref: str,
    write: dict[str, str] | None = None,
    delete: list[str] | None = None,
    *,
    saved: list[dict],
) -> Verdict:
    """Tiers 1-3 plus impact on the draft, publishing nothing.

    This is the editor's Save. It answers the one question local disk cannot: whether Cube would
    accept the model this draft produces. Cube only compiles what is in the store, so the draft's
    change set — plus any overlay — is written onto a throwaway ref created from `ref`, asked
    about there, and the ref is deleted. The same shape `publish.install` uses for its candidate,
    and for the same reason: nothing that answers a query moves until something has compiled.

    A clean draft with no overlay still goes through the throwaway ref, which compiles a copy of
    `ref` and so answers about `ref` itself. One path rather than two, because the branch that
    matters is the one with edits in it and a second path would be the one nobody exercises.

    `saved` is the stored queries this change is checked against, and it is keyword-only and
    has no default on purpose: the queries live in `core.boards`, which this package may not
    read, and a default of `()` would report "nothing breaks" for a change that breaks a board.

    It is one function on purpose. Deleting a stale ref, creating one, writing to it, asking Cube,
    checking saved queries and deleting it again is a single thing that happens once in one order,
    and splitting it would mean reading five functions to follow one Save.
    """
    staged = await stage(store, ref, write, delete)
    if not staged.ok:
        return Verdict(
            False, staged.kind, staged.reason, errors=staged.errors, warnings=staged.warnings
        )

    # The draft's own change set is what the preview ref is missing — it was created from the ref
    # as committed, and the draft has moved on from that. The overlay lands on top.
    pending = drafts.changes(ref)
    writing = {**pending.write, **staged.write}
    removing = [path for path in pending.delete if path not in writing] + [
        path for path in staged.delete if path not in pending.delete
    ]

    async with _previewing:
        try:
            # Cleared on the way in as well as out, because a crashed Save can leave one behind
            # and creating over it would compile whatever it happens to contain. Suppressed
            # rather than handled: if it is still there, `create_ref` below is what refuses, and
            # its message names the ref.
            with suppress(StoreError):
                await store.delete_ref(PREVIEW)
            await store.create_ref(PREVIEW, ref)
            await store.edit(
                PREVIEW,
                write=writing,
                delete=removing,
                message=f"preview of an unpublished change to {ref}",
            )
            validation = await compiles(ref=PREVIEW)
            broken = None
            if validation.compiled:
                broken = impact.check(await served_catalog(ref=PREVIEW), saved)
            return Verdict(
                ok=validation.compiled,
                warnings=staged.warnings,
                compiled=validation.compiled,
                compile_error=validation.compile_error,
                member_errors=validation.member_errors,
                broken=broken,
            )
        except StoreError as exc:
            # One address, two things behind it, and the difference is the whole message.
            # `compiles` swallows a *compile* error into `compile_error`, so a failure from the
            # Cube half is Cube being unreachable — not the edit's fault, and it must not be
            # reported as one.
            said = f"Cube did not answer: {exc}" if exc.where == "cube" else str(exc)
            return Verdict(False, "store", said, warnings=staged.warnings)
        finally:
            # The verdict is already known, so a ref nothing reads and the next Save deletes
            # anyway must not turn a passing Save into a failure.
            with suppress(StoreError):
                await store.delete_ref(PREVIEW)


async def publish(store: ModelStore, ref: str, message: str = "edit model") -> Outcome:
    """Tiers 1-2, then the draft as one commit on its ref. This is the deploy.

    The gate runs again rather than trusting a `Verdict`. A draft is edited between Save and
    Publish like any other file on disk, so what is published is not provably what was verified —
    and the gate is the only thing keeping YAML Cube will refuse out of a ref Cube is serving.
    There is no restart afterwards to hide behind.

    One commit and not one per file, so the history reads as one change and a reviewer's diff is
    the change rather than five of them. The draft is re-synced afterwards at the revision the
    store answered with: it is now identical to the ref, so nothing shows as dirty.
    """
    try:
        staged = await stage(store, ref)
        if not staged.ok:
            return staged
        pending = drafts.changes(ref)
        if not pending.dirty:
            return Outcome(True, warnings=staged.warnings)
        rev = await store.edit(ref, write=pending.write, delete=pending.delete, message=message)
        drafts.sync(ref, drafts.files(ref), rev)
        return Outcome(True, warnings=staged.warnings, write=pending.write, delete=pending.delete)
    except StoreError as exc:
        return Outcome(False, "store", str(exc))


async def land(store: ModelStore, ref: str) -> str:
    """Merge the ref into main, and say how it had to be done.

    The store fast-forwards only, so once main moves — which it does whenever the pipeline is
    re-run, rewriting every generated file — a ref can no longer land as it is. Three steps, in
    increasing cost, and the last one is why a conflict never has to be resolved by hand:

    1. Fast-forward. Nothing moved under it.
    2. Rebase onto main, then fast-forward. History is preserved.
    3. Delete the ref, recreate it from main, and re-commit its files. The ref's own content wins
       by construction, at the cost of its intermediate commits — an audit trail rather than a
       merge base, and `edits.check` has already refused anything main would not accept.

    Refuses a ref whose draft is unpublished: those bytes are invisible to the store and would be
    silently left behind, which is the same reason the store refuses to merge a dirty worktree.
    """
    if drafts.changes(ref).dirty:
        raise StoreError(f"{ref} has unpublished changes — publish them before merging")
    try:
        await store.merge(ref)
        return "merged"
    except StoreError as exc:
        if exc.status != 409:
            raise
    try:
        await store.rebase(ref)
        await store.merge(ref)
        return "rebased"
    except StoreError as exc:
        if exc.status != 409:
            raise

    files = await store.files(ref, refresh=True)
    await store.delete_ref(ref)
    await store.create_ref(ref, MAIN)
    rev = await store.replace(ref, files, f"replay {ref} onto {MAIN}")
    drafts.sync(ref, files, rev)
    await store.merge(ref)
    return "replayed"
