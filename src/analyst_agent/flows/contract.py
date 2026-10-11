"""What `flow.yml` and `phase.yml` say, and every way they can be wrong.

`extra="forbid"` throughout, and that is the reason the machine-read half is YAML rather than
prose: a clumsy sentence in `CONTEXT.md` is only a clumsy sentence, but `revieW: none` would be a
**Gate** that quietly stopped existing. A misspelled field has to name itself.

Nothing here raises at a caller. A refusal is a `Report`, so the authoring agent is told what is
wrong with the file it just wrote in the same words an HTTP route would use — the same shape as a
gate rejection reaching the query agent as text it can act on.

`Report` and not `Outcome` or `Verdict`: both of those are `modelstore.editing.workspace`'s, and
this package would be the second spelling of each.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from ..modelstore.schema import CubeQuery
from . import layout

BUNDLES = ("query", "python")
"""The closed menu a Phase may ask for. Closed so a tool can be renamed without breaking a file
somebody wrote, which is why a Phase names a Bundle and never a tool. `write_output` is in no
Bundle — every agent Phase has it, because producing its declared outputs is what being one
means. `dashboards` is deliberately absent: a Flow that silently rewrites a board every Tuesday
is a surprise nobody asked for."""


@dataclass
class Report:
    """Whether a Flow may be run, or a file written, and why not.

    `kind` is what a route turns into a status code — `layout`, `field`, `order`, `path`,
    `missing` — and is empty when nothing was refused. Warnings do not refuse: a Phase declaring
    no outputs is odd rather than wrong, and refusing it would stop someone part-way through
    writing one.
    """

    ok: bool
    kind: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Input(BaseModel):
    """One declared input: exactly one of `reference:` or `working:`.

    Two fields rather than a `kind`/`path` pair because the YAML a person writes is
    `- reference: _config/voice.md`, and the file should read as the sentence it is. Which one is
    set decides which root the path resolves against, so "neither" and "both" are refusals rather
    than defaults — a Working path silently resolved against the Flow would read the definition
    instead of this Run's work.
    """

    model_config = ConfigDict(extra="forbid")

    reference: str = ""
    working: str = ""

    @model_validator(mode="after")
    def _exactly_one(self):
        if bool(self.reference) == bool(self.working):
            raise ValueError("an input names exactly one of 'reference:' or 'working:'")
        return self

    @property
    def kind(self) -> str:
        return "reference" if self.reference else "working"

    @property
    def path(self) -> str:
        return self.reference or self.working


class PhaseSpec(BaseModel):
    """`phase.yml`.

    `review` defaults to `required` because the **Gate** is what a Flow is *for*: a Flow whose
    author forgot the field should pause, not run past the person who wanted to read it.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["agent", "script"]
    review: Literal["required", "none"] = "required"
    tools: list[Literal["query", "python"]] = []
    inputs: list[Input] = []
    outputs: list[str] = []
    # Declared outputs for which no rows is a correct answer — a transfer plan when no store is
    # short. Every other declared .csv/.tsv fails the Phase when it holds only a header, because
    # an empty file the next Phase reads as "there is nothing" is usually a Phase that failed
    # quietly. Opt-in per file, so the exception is written down where the output is declared.
    may_be_empty: list[str] = []
    # `kind: script` only — the one file it runs.
    script: str = ""
    # `kind: agent` only — work prepared when the Flow was written, so a Run does not re-derive
    # it. Each query is `queries/<name>.json` and writes `<name>.csv`; each script is a `.py` in
    # the Phase's folder. The agent runs them as they are, and writes its own only when the Brief
    # or the data needs something they do not do.
    queries: list[str] = []
    scripts: list[str] = []

    @model_validator(mode="after")
    def _kind_agrees(self):
        if self.kind == "script":
            if not self.script:
                raise ValueError("a script Phase needs 'script:' naming the .py file it runs")
            if self.tools:
                raise ValueError("a script Phase runs no model, so it cannot ask for tools")
        if self.kind == "agent" and self.script:
            raise ValueError(
                "'script:' belongs to a script Phase — an agent Phase lists its prepared scripts "
                "under 'scripts:'"
            )
        if self.kind == "script" and (self.queries or self.scripts):
            raise ValueError(
                "a script Phase runs its one 'script:' — 'queries:' and 'scripts:' "
                "are an agent Phase's prepared work"
            )
        # A prepared query is run by the `query` Bundle and a prepared script by `python`; a Phase
        # without the Bundle would be handed work it has no tool to do.
        if self.queries and "query" not in self.tools:
            raise ValueError("'queries:' needs the query Bundle — add query to 'tools:'")
        if self.scripts and "python" not in self.tools:
            raise ValueError("'scripts:' needs the python Bundle — add python to 'tools:'")
        misnamed = [name for name in self.queries if not name.endswith(".json") or "/" in name]
        misnamed += [name for name in self.scripts if not name.endswith(".py") or "/" in name]
        if misnamed:
            raise ValueError(
                f"{', '.join(misnamed)}: a query is a name.json in queries/, a script a name.py "
                "beside phase.yml"
            )
        undeclared = [name for name in self.may_be_empty if name not in self.outputs]
        if undeclared:
            raise ValueError(
                f"may_be_empty names {', '.join(undeclared)}, which 'outputs:' does not declare"
            )
        return self


class FlowSpec(BaseModel):
    """`flow.yml`. Deliberately thin: the Phases are folders, not a list here.

    A `phases:` key would be a second statement of the execution order, and the folder numbering
    is the first one. Two of them is one that can disagree.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""


@dataclass
class Phase:
    """A located Phase: what its folder says, and what its folder is called."""

    number: int
    dir_name: str
    directory: Path
    spec: PhaseSpec
    # `CONTEXT.md`, verbatim and unparsed. Empty for a script Phase, which has no prompt.
    prose: str = ""


@dataclass
class Flow:
    name: str
    directory: Path
    spec: FlowSpec
    prose: str = ""
    phases: list[Phase] = field(default_factory=list)


def _read_yaml(path: Path) -> tuple[dict, str]:
    """(mapping, error). A missing or unparsable file is an error string, never an exception —
    the caller is collecting refusals, not handling them one at a time."""
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}, f"{path.name} is missing"
    except (yaml.YAMLError, OSError) as exc:
        return {}, f"{path.name} could not be read: {exc}"
    if not isinstance(raw, dict):
        return {}, f"{path.name} must be a mapping of fields, not {type(raw).__name__}"
    return raw, ""


def _said(exc: ValidationError) -> list[str]:
    """Pydantic's complaint, as lines naming the field. `loc` first because "tools.0" is the
    thing someone has to go and edit."""
    lines = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "(top level)"
        lines.append(f"{where}: {error['msg']}")
    return lines


def load(directory: Path) -> tuple[Flow | None, Report]:
    """Parse a Flow's folder, or say what is wrong with it.

    Everything checkable without a Run is checked here, and this is the whole list: fields and
    their spellings, Bundle names, unique numbering, a `.py` present for a script Phase, a
    `CONTEXT.md` present for an agent Phase, a **Reference** input that exists, and a **Working**
    input naming an earlier Phase.

    What is deliberately *not* checked is whether a Working input exists, because it does not:
    it names a file a Run has yet to produce. Existence is the runner's check, and the split is
    why this can be called on every write while the authoring agent is mid-thought.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not directory.is_dir():
        return None, Report(False, "missing", [f"{directory.name} is not a flow folder"])

    raw, said = _read_yaml(directory / layout.FLOW_YML)
    if said:
        return None, Report(False, "missing", [said])
    try:
        spec = FlowSpec.model_validate(raw)
    except ValidationError as exc:
        return None, Report(False, "field", [f"{layout.FLOW_YML} — {line}" for line in _said(exc)])

    prose_path = directory / layout.CONTEXT_MD
    prose = prose_path.read_text() if prose_path.is_file() else ""
    if not prose.strip():
        warnings.append(
            f"{layout.CONTEXT_MD} is missing or empty — a Flow should say what it is for"
        )

    dir_names = layout.phase_dirs(directory)
    if not dir_names:
        errors.append(f"{layout.PHASES}/ holds no NN_name folders, so there is nothing to run")

    phases: list[Phase] = []
    for dir_name in dir_names:
        here = layout.phases_dir(directory) / dir_name
        number = layout.phase_number(dir_name)
        assert number is not None  # `phase_dirs` returned it, so it parsed
        raw, said = _read_yaml(here / layout.PHASE_YML)
        if said:
            errors.append(f"{dir_name}: {said}")
            continue
        try:
            phase_spec = PhaseSpec.model_validate(raw)
        except ValidationError as exc:
            errors.extend(f"{dir_name}/{layout.PHASE_YML} — {line}" for line in _said(exc))
            continue

        phase_prose = ""
        context = here / layout.CONTEXT_MD
        if phase_spec.kind == "agent":
            if not context.is_file() or not context.read_text().strip():
                errors.append(f"{dir_name}: an agent Phase needs a {layout.CONTEXT_MD} to run on")
            else:
                phase_prose = context.read_text()
        elif not (here / phase_spec.script).is_file():
            errors.append(f"{dir_name}: {phase_spec.script} is named by phase.yml but not there")

        for name in phase_spec.scripts:
            if not (here / name).is_file():
                errors.append(f"{dir_name}: script {name} is named by phase.yml but not there")
        # Tier 1 only — the shape of a Cube query. Whether its members exist needs the served
        # model, which this function never asks for; `validate_flow` does that half.
        for name in phase_spec.queries:
            target = here / layout.QUERIES / name
            if not target.is_file():
                errors.append(f"{dir_name}: query {layout.QUERIES}/{name} is not there")
                continue
            try:
                CubeQuery.model_validate(json.loads(target.read_text()))
            except (ValueError, ValidationError) as exc:
                first = _said(exc)[0] if isinstance(exc, ValidationError) else str(exc)
                errors.append(f"{dir_name}: query {name} is not a Cube query — {first}")

        if not phase_spec.outputs:
            warnings.append(
                f"{dir_name}: declares no outputs, so it hands nothing to the next Phase"
            )
        # Prepared work is the default, and the Brief is the one thing that can call for something
        # else — so a Phase that never reads it runs its prepared work whatever a Run asks. Seen
        # live: a brief asking for a store breakdown reached only the writing Phase, which could
        # say the data was missing and nothing more. A warning: a fixed pull is a legitimate design.
        reads_brief = any(
            declared.kind == "working" and declared.path == layout.BRIEF_MD
            for declared in phase_spec.inputs
        )
        if (phase_spec.queries or phase_spec.scripts) and not reads_brief:
            warnings.append(
                f"{dir_name}: has prepared work but does not read {layout.BRIEF_MD}, so a brief "
                "cannot change what it pulls or computes — add `- working: brief.md` if it should"
            )

        phases.append(Phase(number, dir_name, here, phase_spec, phase_prose))

    seen: dict[int, str] = {}
    for phase in phases:
        if phase.number in seen:
            errors.append(
                f"{phase.dir_name} and {seen[phase.number]} are both Phase {phase.number} — "
                "the number is the execution order, so it has to be unique"
            )
        seen[phase.number] = phase.dir_name

    errors.extend(_input_problems(directory, phases))

    kind = "field" if errors else ""
    if errors and any("is missing" in line or "not there" in line for line in errors):
        kind = "missing"
    return (
        Flow(directory.name, directory, spec, prose, phases),
        Report(not errors, kind, errors, warnings),
    )


def _input_problems(directory: Path, phases: list[Phase]) -> list[str]:
    """Every input refusal that does not need a Run.

    A **Reference** is checked for real: it is part of the definition, so it is there now or the
    Flow is broken now. A **Working** input can only be checked structurally — it must name an
    *earlier* Phase's folder or the Brief, since a Phase reading a later Phase's output is reading
    something that cannot exist yet, and that is a Flow whose order is wrong rather than a Run
    that failed.
    """
    problems = []
    for phase in phases:
        for declared in phase.spec.inputs:
            if declared.kind == "reference":
                resolved = layout.resolve_input(directory, directory, "reference", declared.path)
                if resolved is None:
                    problems.append(f"{phase.dir_name}: {declared.path} leaves the flow folder")
                elif not resolved.is_file():
                    problems.append(f"{phase.dir_name}: reference {declared.path} is not there")
                continue

            head = declared.path.split("/")[0]
            if head == layout.BRIEF_MD:
                continue
            number = layout.phase_number(head)
            if number is None:
                problems.append(
                    f"{phase.dir_name}: working input {declared.path} names neither "
                    f"{layout.BRIEF_MD} nor a Phase folder"
                )
            elif number >= phase.number:
                problems.append(
                    f"{phase.dir_name}: working input {declared.path} is from Phase {number}, "
                    "which does not run before this one"
                )
    return problems
