from dataclasses import dataclass

from langchain.tools import tool

from ...cube.client import CubeClient, CubeError
from ...datamodel import grounding
from ...datamodel.editing import impact, workspace
from ...datamodel.modelgen import publish
from ...modelstore import StoreError
from ...modelstore.client import ModelStore
from .trace import end, start

EVENT_KEYS = frozenset({"tool", "label", "ok", "note", "errors"})
"""Every key an editor tool's event may carry, asserted by a test. The file bodies are absent
on purpose: a transcript records that a file was written, and the ref records what into."""


@dataclass
class EditContext:
    client: CubeClient
    store: ModelStore
    ref: str


def build_editor_tools(ctx: EditContext) -> list:
    def _refused(tool_name: str, outcome: workspace.Outcome) -> str:
        # `kind` is what the routes turn into a status code; here it only decides whether to
        # name the next step.
        end(tool_name, False, errors=outcome.errors)
        if outcome.kind == "ref":
            return (
                f"{outcome.reason}. Tell the user to create a ref on the data page before you "
                "edit anything."
            )
        return outcome.reason

    @tool
    async def list_model_files() -> str:
        """List the model's YAML files and the ref they are on. Call this first — every other
        tool takes a path, and paths are not guessable."""
        try:
            start("list_model_files", "Listing the model files")
            names = sorted(workspace.model_yaml(await ctx.store.files(ctx.ref, refresh=True)))
            end("list_model_files", True, note=f"{len(names)} files on {ctx.ref}")
            return f"ref: {ctx.ref}\n" + "\n".join(f"- {name}" for name in names)
        except StoreError as exc:
            end("list_model_files", False, errors=[str(exc)])
            return f"Could not read the model: {exc}"

    @tool
    async def read_model_file(path: str) -> str:
        """Read one model file, e.g. `cubes/invoice_line.yml` or `views/sales.yml`."""
        try:
            start("read_model_file", f"Reading {path}")
            relative, refused = workspace.resolve(path)
            if not relative:
                end("read_model_file", False)
                return refused
            text = await ctx.store.file(relative, ctx.ref)
            end("read_model_file", True, note=relative)
            return text
        except StoreError:
            end("read_model_file", False)
            return f"No model file at {path!r}. Call list_model_files."

    @tool
    async def write_model_file(path: str, text: str) -> str:
        """Replace one model file with `text`, the whole file as YAML.

        Cubes are append-only: you may add measures, dimensions and segments, and edit
        `description`, `title`, `meta` and `format` on what exists — you may not rename or
        remove anything a cube already has, or change its `sql`, `type` or `sql_table`. Views
        are yours to rewrite. A rejection comes back with the reason; fix it and call again.
        """
        start("write_model_file", f"Writing {path}")
        outcome = await workspace.write(ctx.store, ctx.ref, path, text)
        if not outcome.ok:
            return _refused("write_model_file", outcome)

        end("write_model_file", True, note=outcome.path, errors=outcome.warnings)
        warnings = "\n".join(f"- {warning}" for warning in outcome.warnings)
        return (
            f"Wrote {outcome.path} to {ctx.ref}. **This is live** — Cube asks the store for this "
            "ref's version on every request, so it is answering with it within seconds.\n"
            + warnings
        ).strip()

    @tool
    async def delete_model_file(path: str) -> str:
        """Delete one model file. Refused when it would remove a cube or any of its members."""
        start("delete_model_file", f"Deleting {path}")
        outcome = await workspace.delete(ctx.store, ctx.ref, path)
        if not outcome.ok:
            return _refused("delete_model_file", outcome)
        end("delete_model_file", True, note=outcome.path)
        return f"Deleted {outcome.path} from {ctx.ref}."

    @tool
    async def validate_model() -> str:
        """Compile the edited model and report what it would break.

        A saved file is already live — Cube polls the store for the ref it is serving — so this
        is not what loads it. It is how you learn whether it compiled and which saved queries it
        broke. Roughly ten seconds.
        """
        start("validate_model", "Compiling the model")
        validation = await publish.check(ref=ctx.ref)
        if not validation.compiled:
            end("validate_model", False, errors=[validation.compile_error[:200]])
            return f"Cube refused the model:\n{validation.compile_error}"

        lines = [f"Compiled. Serving {len(validation.entities)} entities."]
        if validation.member_errors:
            lines.append("Members that fail at query time:")
            lines += [
                f"- {member}: {problem}" for member, problem in validation.member_errors.items()
            ]

        try:
            catalog = await ctx.client.catalog(refresh=True)
            # This ref's grounding, not main's: its certified queries are among the saved
            # queries an edit here can break.
            grounded = await grounding.load(ctx.ref)
        except (CubeError, StoreError) as exc:
            end("validate_model", True, note="compiled; impact unread")
            return "\n".join([*lines, f"Could not check saved queries: {exc}"])

        report = await impact.check(catalog, grounded)
        end(
            "validate_model",
            True,
            note=f"{len(validation.entities)} entities, {len(report.broken)} queries broken",
        )
        lines.append(report.as_message() or f"No saved query breaks — {report.checked} checked.")
        return "\n".join(lines)

    return [
        list_model_files,
        read_model_file,
        write_model_file,
        delete_model_file,
        validate_model,
    ]
