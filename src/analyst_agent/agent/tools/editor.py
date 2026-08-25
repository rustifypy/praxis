"""The editor agent's five tools. It reads the model, proposes changes, and compiles them.

**It writes nothing.** Every change it makes goes into an overlay held for the turn and is
emitted as a `proposal` event; a person accepts it hunk by hunk in the editor, and the editor's
own Save and Commit are what reach the store. That is the whole reason the overlay exists — an
agent whose writes were commits gave a person nothing to approve, because Cube was already
serving them by the time the sentence explaining them was written.

The overlay is also why `read_model_file` reads it before the store: a second proposal in the
same turn has to build on the first, or "and add another one" quietly reverts the first.
"""

from dataclasses import dataclass, field

from langchain.tools import tool

from ...core import boards
from ...core.events import Event, emit
from ...modelstore import StoreError, drafts
from ...modelstore.client import ModelStore
from ...modelstore.editing import workspace
from .trace import end, start

EVENT_KEYS = frozenset({"tool", "label", "ok", "note", "errors"})
"""Every key an editor tool's `tool_start`/`tool_end` may carry, asserted by a test.

File bodies are absent on purpose, and still are: a proposed body travels on its own `proposal`
event, so the trace stays a record of what happened rather than a second copy of the model.
"""


@dataclass
class EditContext:
    """What the editor agent is editing: one store, one ref, and its own proposals so far.

    The **draft** is the model the person is looking at, so that is what every tool reads. What
    `staged` adds is only this turn's proposals, which are on no ref and in no file — the person
    has not accepted them yet. Without it the agent's second proposal in a turn is gated against
    a model that does not include its first.

    The store is here for `validate_model` alone, which asks through `workspace.verify` — that
    compiles on a throwaway ref of its own, so a session pinned to this ref would be pointed at
    the wrong model.
    """

    store: ModelStore
    ref: str
    # path -> proposed body. Deletions are paths mapped to None, so one dict carries both and a
    # proposal that re-creates a file it deleted earlier in the turn is just another entry.
    staged: dict[str, str | None] = field(default_factory=dict)

    async def current(self) -> dict[str, str]:
        """The model as the person sees it: the draft, with this turn's proposals laid over it."""
        await workspace.ensure(self.store, self.ref)
        files = drafts.files(self.ref)
        for path, body in self.staged.items():
            if body is None:
                files.pop(path, None)
            else:
                files[path] = body
        return files

    def change_set(self) -> tuple[dict[str, str], list[str]]:
        """The overlay split the way `workspace` takes it: what to write, what to remove."""
        write = {path: body for path, body in self.staged.items() if body is not None}
        return write, [path for path, body in self.staged.items() if body is None]


def build_editor_tools(ctx: EditContext) -> list:
    def _refused(tool_name: str, outcome: workspace.Outcome) -> str:
        # `kind` is what the routes turn into a status code; here it only decides whether to
        # name the next step.
        end(tool_name, False, errors=outcome.errors)
        if outcome.kind == "ref":
            return (
                f"{outcome.reason}. Tell the user to create a branch on the data page before you "
                "edit anything."
            )
        return outcome.reason

    async def _gated(path: str, body: str | None) -> workspace.Outcome:
        """Tiers 1-2 on the overlay as it *would* be with this one change, applying nothing.

        Two callers, propose and propose-delete, which is why it is a function: the alternative
        was the same five lines twice, differing only in whether the body is None.
        """
        proposed = {**ctx.staged, path: body}
        return await workspace.stage(
            ctx.store,
            ctx.ref,
            {name: text for name, text in proposed.items() if text is not None},
            [name for name, text in proposed.items() if text is None],
        )

    @tool
    async def list_model_files() -> str:
        """List the model's files and the ref they are on. Call this first — every other tool
        takes a path, and paths are not guessable."""
        try:
            start("list_model_files", "Listing the model files")
            files = await ctx.current()
            names = sorted(path for path in files if workspace.resolve(path)[0])
            end("list_model_files", True, note=f"{len(names)} files on {ctx.ref}")
            return f"ref: {ctx.ref}\n" + "\n".join(f"- {name}" for name in names)
        except StoreError as exc:
            end("list_model_files", False, errors=[str(exc)])
            return f"Could not read the model: {exc}"

    @tool
    async def read_model_file(path: str) -> str:
        """Read one model file, e.g. `cubes/invoice_line.yml` or `views/sales.yml`.

        This is the file as it stands *including changes not yet published* — your own proposals
        this turn, and anything the user has saved locally. Read before you propose: a proposal
        replaces the whole file.
        """
        try:
            start("read_model_file", f"Reading {path}")
            relative, refused = workspace.resolve(path)
            if not relative:
                end("read_model_file", False)
                return refused
            files = await ctx.current()
            if relative not in files:
                end("read_model_file", False)
                return f"No model file at {path!r}. Call list_model_files."
            end("read_model_file", True, note=relative)
            return files[relative]
        except StoreError as exc:
            end("read_model_file", False, errors=[str(exc)])
            return f"Could not read the model: {exc}"

    @tool
    async def propose_model_file(path: str, text: str) -> str:
        """Propose replacing one model file with `text`, the whole file.

        **Nothing is written by this.** The user sees your version as a diff and accepts it line
        by line; their Save is what compiles it and their Publish is what deploys it. So say what
        you changed and why — they are reading the diff, not taking your word for it.

        Cubes are append-only: you may add measures, dimensions and segments, and edit
        `description`, `title`, `meta` and `format` on what exists — you may not rename or remove
        anything a cube already has, or change its `sql`, `type` or `sql_table`. Views are yours
        to rewrite. A rejection comes back
        with the reason; fix it and call again.
        """
        start("propose_model_file", f"Proposing {path}")
        relative, refused = workspace.resolve(path)
        if not relative:
            end("propose_model_file", False)
            return refused

        # The gate runs on the proposal even though nothing is being written, because a refusal
        # is only useful while the agent is still holding the reason it wrote that YAML. Told at
        # Save instead, it is the person who has to explain it back.
        outcome = await _gated(relative, text)
        if not outcome.ok:
            return _refused("propose_model_file", outcome)

        ctx.staged[relative] = text
        # The one event in this toolset that carries a body: nothing else holds it, since no ref
        # has it and there is no file on disk to point at.
        emit(Event(type="proposal", data={"path": relative, "text": text, "ref": ctx.ref}))
        end("propose_model_file", True, note=relative, errors=outcome.warnings)
        warnings = "\n".join(f"- {warning}" for warning in outcome.warnings)
        return (
            f"Proposed {relative}. It is waiting for the user to accept it — nothing is saved and "
            f"Cube is still serving {ctx.ref} as it was.\n" + warnings
        ).strip()

    @tool
    async def propose_delete(path: str) -> str:
        """Propose deleting one model file. Refused when it would remove a cube or its members.

        Like `propose_model_file`, this is a proposal: the file is still there until the user
        accepts and saves.
        """
        start("propose_delete", f"Proposing to delete {path}")
        relative, refused = workspace.resolve(path)
        if not relative:
            end("propose_delete", False)
            return refused
        if relative not in await ctx.current():
            end("propose_delete", False)
            return f"No model file at {path!r}. Call list_model_files."

        outcome = await _gated(relative, None)
        if not outcome.ok:
            return _refused("propose_delete", outcome)

        ctx.staged[relative] = None
        emit(Event(type="proposal", data={"path": relative, "text": None, "ref": ctx.ref}))
        end("propose_delete", True, note=relative)
        return f"Proposed deleting {relative}. It is waiting for the user to accept it."

    @tool
    async def validate_model() -> str:
        """Compile everything proposed so far and report what it would break.

        This is the only way to learn whether your proposals actually compile: they are not on any
        ref, so Cube has never seen them. It compiles them on a throwaway ref, which takes roughly
        ten seconds. Call it once a change is coherent, not after every file — and call it before
        you say you are done, because otherwise the user finds out at Save.
        """
        start("validate_model", "Compiling the proposed model")
        write, delete = ctx.change_set()
        saved = await boards.saved_queries()
        verdict = await workspace.verify(ctx.store, ctx.ref, write, delete, saved=saved)
        if verdict.kind and verdict.kind != "rejected":
            end("validate_model", False, errors=[verdict.reason[:200]])
            return verdict.reason
        if not verdict.compiled:
            end("validate_model", False, errors=[verdict.compile_error[:200]])
            return f"Cube refused the model:\n{verdict.compile_error or verdict.reason}"

        lines = ["Compiled."]
        if verdict.member_errors:
            lines.append("Members that fail at query time:")
            lines += [f"- {member}: {problem}" for member, problem in verdict.member_errors.items()]

        broken = len(verdict.broken.broken) if verdict.broken else 0
        end("validate_model", True, note=f"compiled, {broken} queries broken")
        if verdict.broken:
            lines.append(
                verdict.broken.as_message()
                or f"No saved query breaks — {verdict.broken.checked} checked."
            )
        return "\n".join(lines)

    return [
        list_model_files,
        read_model_file,
        propose_model_file,
        propose_delete,
        validate_model,
    ]
