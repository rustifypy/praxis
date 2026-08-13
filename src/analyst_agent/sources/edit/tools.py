"""The model-editor tools. **Nothing here merges, switches branch or creates one** — those
restart the container and land work, so they stay buttons on the page.
"""

from dataclasses import dataclass
from pathlib import Path

from ...chat.events import Event, emit
from ...cube.client import CubeClient, CubeError
from ...dashboards.store import DashboardStore
from ...grounding import for_source
from ..model import publish, vcs
from ..registry import Source
from . import impact, workspace

EVENT_KEYS = frozenset({"tool", "label", "ok", "note", "errors"})
"""Every key an editor tool's event may carry — asserted by a test. Absent on purpose: the
file bodies. A transcript records that a file was written, and the branch records what into."""


@dataclass
class EditContext:
    """One source's model, on whatever branch is checked out."""

    source: Source
    client: CubeClient
    dashboards: DashboardStore

    @property
    def repo(self) -> Path:
        return self.source.model_dir()


def _start(tool_name: str, label: str) -> None:
    emit(Event(type="tool_start", data={"tool": tool_name, "label": label}))


def _end(tool_name: str, ok: bool, note: str = "", errors: list[str] | None = None) -> None:
    data: dict = {"tool": tool_name, "ok": ok}
    if note:
        data["note"] = note
    if errors:
        data["errors"] = errors
    emit(Event(type="tool_end", data=data))


def build_editor_tools(ctx: EditContext) -> list:
    from langchain.tools import tool

    def _refused(tool_name: str, outcome: workspace.Outcome) -> str:
        """A refusal as something the model can act on. `kind` is what the routes turn into a
        status code; here it only decides whether to name the next step."""
        _end(tool_name, False, errors=outcome.errors)
        if outcome.kind == "branch":
            return (
                f"{outcome.reason}. Tell the user to create a branch on the source page before "
                "you edit anything."
            )
        return outcome.reason

    @tool
    async def list_model_files() -> str:
        """List the model's YAML files and the branch they are on. Call this first — every
        other tool takes a path, and paths are not guessable."""
        _start("list_model_files", "Listing the model files")
        branch = await vcs.current(ctx.repo)
        names = sorted(workspace.files(ctx.source))
        _end("list_model_files", True, note=f"{len(names)} files on {branch}")
        dirty = await vcs.status(ctx.repo)
        return f"branch: {branch}\nuncommitted: {', '.join(dirty) or 'nothing'}\n" + "\n".join(
            f"- {name}" for name in names
        )

    @tool
    async def read_model_file(path: str) -> str:
        """Read one model file, e.g. `cubes/invoice_line.yml` or `views/sales.yml`."""
        _start("read_model_file", f"Reading {path}")
        target, _ = workspace.resolve(ctx.source, path)
        if target is None or not target.is_file():
            _end("read_model_file", False)
            return f"No model file at {path!r}. Call list_model_files."
        _end("read_model_file", True, note=path)
        return target.read_text()

    @tool
    async def write_model_file(path: str, text: str) -> str:
        """Replace one model file with `text`, the whole file as YAML.

        Cubes are append-only: you may add measures, dimensions and segments, and edit
        `description`, `title`, `meta` and `format` on what exists — you may not rename or
        remove anything a cube already has, or change its `sql`, `type` or `sql_table`. Views
        are yours to rewrite. A rejection comes back with the reason; fix it and call again.
        """
        _start("write_model_file", f"Writing {path}")
        outcome = await workspace.write(ctx.source, path, text)
        if not outcome.ok:
            return _refused("write_model_file", outcome)

        _end("write_model_file", True, note=outcome.path, errors=outcome.warnings)
        warnings = "\n".join(f"- {w}" for w in outcome.warnings)
        return (
            f"Wrote {outcome.path}. **This is live** — the container watches these files in dev "
            "mode, so the source is already answering with it.\n" + warnings
        ).strip()

    @tool
    async def delete_model_file(path: str) -> str:
        """Delete one model file. Refused when it would remove a cube or any of its members."""
        _start("delete_model_file", f"Deleting {path}")
        outcome = await workspace.delete(ctx.source, path)
        if not outcome.ok:
            return _refused("delete_model_file", outcome)
        _end("delete_model_file", True, note=outcome.path)
        return f"Deleted {outcome.path}."

    @tool
    async def validate_model() -> str:
        """Compile the edited model and report what it would break.

        A saved file is already live, so this is not what loads it — it is how you learn
        whether it compiled and which saved queries it broke. Roughly ten seconds.
        """
        _start("validate_model", "Restarting Cube and compiling the model")
        from .. import docker

        if not (await docker.restart(ctx.source.service)).ok:
            _end("validate_model", False)
            return f"Could not restart {ctx.source.service}."

        validation = await publish.check(ctx.source)
        if not validation.compiled:
            _end("validate_model", False, errors=[validation.compile_error[:200]])
            return f"Cube refused the model:\n{validation.compile_error}"

        lines = [f"Compiled. Serving {len(validation.entities)} entities."]
        if validation.member_errors:
            lines.append("Members that fail at query time:")
            lines += [f"- {m}: {e}" for m, e in validation.member_errors.items()]

        try:
            catalog = await ctx.client.catalog(refresh=True)
        except CubeError as exc:
            _end("validate_model", True, note="compiled; impact unread")
            return "\n".join([*lines, f"Could not check saved queries: {exc}"])

        report = await impact.check(
            ctx.source.name, catalog, ctx.dashboards, for_source(ctx.source)
        )
        _end(
            "validate_model",
            True,
            note=f"{len(validation.entities)} entities, {len(report.broken)} queries broken",
        )
        lines.append(report.as_message() or f"No saved query breaks — {report.checked} checked.")
        return "\n".join(lines)

    @tool
    async def commit_changes(message: str) -> str:
        """Commit the edits so far, with a one-line message saying what changed and why."""
        _start("commit_changes", message[:60])
        if refusal := await workspace.editable(ctx.source):
            return _refused("commit_changes", refusal)
        result = await vcs.commit(ctx.repo, message)
        if not result.ok:
            _end("commit_changes", False, errors=[result.message])
            return f"Could not commit: {result.message}"
        _end("commit_changes", True, note=message[:60])
        return "Committed. Merging is the user's — tell them it is ready to review."

    return [
        list_model_files,
        read_model_file,
        write_model_file,
        delete_model_file,
        validate_model,
        commit_changes,
    ]
