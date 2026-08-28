"""The deterministic half of Flows: the folder, the contract, and a Run's state.

No services and no model here. What is asserted is every refusal a Flow can earn without being
run, the ordering the folder names carry, and the transitions `run.json` records — including
**Stale**, which is the one a person will not notice going wrong.

A real Run needs both a model store and a sandbox, so it is verified by running one.
"""

import textwrap
from pathlib import Path

from analyst_agent.flows import contract, layout, state


def build(root: Path, phases: dict[str, str], *, flow_yml: str = "title: Weekly", **files) -> Path:
    """A flow folder on disk. `phases` maps `NN_name` to its `phase.yml` body; `files` maps any
    other path in the flow to its contents, so a test names only what it is about."""
    flow = root / "weekly"
    (flow / layout.PHASES).mkdir(parents=True)
    (flow / layout.FLOW_YML).write_text(flow_yml)
    (flow / layout.CONTEXT_MD).write_text("Weekly revenue, start to finish.")
    for dir_name, body in phases.items():
        here = flow / layout.PHASES / dir_name
        here.mkdir(parents=True, exist_ok=True)
        (here / layout.PHASE_YML).write_text(textwrap.dedent(body).strip() + "\n")
    for path, text in files.items():
        target = flow / path.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return flow


AGENT = """
kind: agent
outputs:
  - summary.md
"""


# --- the folder ---------------------------------------------------------------


def test_execution_order_is_the_numbering_not_the_string(tmp_path):
    flow = build(
        tmp_path,
        {"01_pull": AGENT, "02_shape": AGENT, "10_write": AGENT},
        **{
            "phases__01_pull__CONTEXT.md": "Pull it.",
            "phases__02_shape__CONTEXT.md": "Shape it.",
            "phases__10_write__CONTEXT.md": "Write it.",
        },
    )
    assert layout.phase_dirs(flow) == ["01_pull", "02_shape", "10_write"]


def test_numbering_may_have_gaps_so_a_step_can_be_inserted_later(tmp_path):
    """`01, 05, 10` leaves room to add a step without renaming its neighbours. Note what does
    *not* work: `015` is fifteen, so it lands after `02` rather than between `01` and `02`."""
    flow = build(
        tmp_path,
        {"01_pull": AGENT, "05_clean": AGENT, "10_write": AGENT},
        **{
            "phases__01_pull__CONTEXT.md": "Pull.",
            "phases__05_clean__CONTEXT.md": "Clean.",
            "phases__10_write__CONTEXT.md": "Write.",
        },
    )
    assert layout.phase_dirs(flow) == ["01_pull", "05_clean", "10_write"]

    inserted = flow / layout.PHASES / "015_late"
    inserted.mkdir()
    (inserted / layout.PHASE_YML).write_text(AGENT)
    (inserted / layout.CONTEXT_MD).write_text("Late.")
    assert layout.phase_dirs(flow) == ["01_pull", "05_clean", "10_write", "015_late"]


def test_a_folder_that_is_not_a_phase_is_ignored_rather_than_refused(tmp_path):
    flow = build(tmp_path, {"01_pull": AGENT}, **{"phases__01_pull__CONTEXT.md": "Pull."})
    (flow / layout.PHASES / "notes").mkdir()
    assert layout.phase_dirs(flow) == ["01_pull"]


def test_an_input_path_cannot_leave_the_root_it_belongs_to(tmp_path):
    flow, run = tmp_path / "weekly", tmp_path / "weekly" / "runs" / "r1"
    assert layout.resolve_input(flow, run, "reference", "../../etc/passwd") is None
    assert layout.resolve_input(flow, run, "working", "01_pull/../../../x") is None
    resolved = layout.resolve_input(flow, run, "reference", "_config/voice.md")
    assert resolved == flow / "_config" / "voice.md"


def test_the_two_roots_are_different_roots(tmp_path):
    """Reference against the Flow, Working against the Run. The bug this refuses is a Working
    path resolving against the definition, which would read the Flow instead of the work."""
    flow, run = tmp_path / "weekly", tmp_path / "weekly" / "runs" / "r1"
    assert layout.resolve_input(flow, run, "reference", "a.md") == flow / "a.md"
    assert layout.resolve_input(flow, run, "working", "a.md") == run / "a.md"


# --- the contract -------------------------------------------------------------


def test_a_well_formed_flow_loads(tmp_path):
    flow = build(
        tmp_path,
        {
            "01_pull": """
                kind: agent
                tools: [query, python]
                outputs:
                  - revenue.csv
                """,
            "02_write": """
                kind: agent
                review: none
                inputs:
                  - reference: _config/voice.md
                  - working: 01_pull/output/revenue.csv
                outputs:
                  - summary.md
                """,
        },
        **{
            "phases__01_pull__CONTEXT.md": "Pull revenue.",
            "phases__02_write__CONTEXT.md": "Write it up.",
            "_config__voice.md": "Plain sentences.",
        },
    )
    loaded, report = contract.load(flow)
    assert report.ok, report.errors
    assert loaded is not None
    assert [p.dir_name for p in loaded.phases] == ["01_pull", "02_write"]
    assert loaded.phases[0].spec.review == "required", "the Gate is the default"
    assert loaded.phases[1].spec.inputs[0].kind == "reference"
    assert loaded.phases[1].spec.inputs[1].kind == "working"
    assert loaded.phases[0].prose == "Pull revenue."


def test_a_misspelled_field_names_itself(tmp_path):
    flow = build(
        tmp_path,
        {"01_pull": "kind: agent\nrevieW: none\n"},
        **{"phases__01_pull__CONTEXT.md": "Pull."},
    )
    _, report = contract.load(flow)
    assert not report.ok
    assert any("revieW" in line for line in report.errors), report.errors


def test_an_unknown_bundle_is_refused(tmp_path):
    flow = build(
        tmp_path,
        {"01_pull": "kind: agent\ntools: [query, warehouse]\n"},
        **{"phases__01_pull__CONTEXT.md": "Pull."},
    )
    _, report = contract.load(flow)
    assert not report.ok
    assert any("tools.1" in line for line in report.errors), report.errors


def test_an_input_names_exactly_one_root(tmp_path):
    both = """
        kind: agent
        inputs:
          - reference: _config/voice.md
            working: 01_pull/output/x.csv
        """
    flow = build(tmp_path, {"02_write": both}, **{"phases__02_write__CONTEXT.md": "Write."})
    _, report = contract.load(flow)
    assert not report.ok
    assert any("exactly one" in line for line in report.errors), report.errors


def test_a_script_phase_needs_its_file_and_no_tools(tmp_path):
    flow = build(tmp_path, {"01_fetch": "kind: script\nscript: fetch.py\n"})
    _, report = contract.load(flow)
    assert not report.ok
    assert any("fetch.py" in line for line in report.errors), report.errors

    flow = build(
        tmp_path / "with-tools",
        {"01_fetch": "kind: script\nscript: fetch.py\ntools: [python]\n"},
        **{"phases__01_fetch__fetch.py": "print('hi')"},
    )
    _, report = contract.load(flow)
    assert not report.ok
    assert any("runs no model" in line for line in report.errors), report.errors


def test_an_agent_phase_needs_prose_to_run_on(tmp_path):
    flow = build(tmp_path, {"01_pull": AGENT})
    _, report = contract.load(flow)
    assert not report.ok
    assert any(layout.CONTEXT_MD in line for line in report.errors), report.errors


def test_two_phases_cannot_share_a_number(tmp_path):
    flow = build(
        tmp_path,
        {"01_pull": AGENT, "01_fetch": AGENT},
        **{"phases__01_pull__CONTEXT.md": "a", "phases__01_fetch__CONTEXT.md": "b"},
    )
    _, report = contract.load(flow)
    assert not report.ok
    assert any("execution order" in line for line in report.errors), report.errors


def test_a_missing_reference_is_refused_but_a_missing_working_input_is_not(tmp_path):
    """The split that lets this run on every write: a Reference is part of the definition and is
    either there now or the Flow is broken now. A Working input names a file no Run has made yet.
    """
    flow = build(
        tmp_path,
        {
            "01_pull": AGENT,
            "02_write": """
                kind: agent
                inputs:
                  - working: 01_pull/output/revenue.csv
                outputs: [summary.md]
                """,
        },
        **{"phases__01_pull__CONTEXT.md": "a", "phases__02_write__CONTEXT.md": "b"},
    )
    _, report = contract.load(flow)
    assert report.ok, report.errors

    flow = build(
        tmp_path / "no-ref",
        {"01_pull": "kind: agent\ninputs:\n  - reference: _config/gone.md\n"},
        **{"phases__01_pull__CONTEXT.md": "a"},
    )
    _, report = contract.load(flow)
    assert not report.ok
    assert any("gone.md" in line for line in report.errors), report.errors


def test_a_working_input_cannot_come_from_a_later_phase(tmp_path):
    flow = build(
        tmp_path,
        {
            "01_pull": """
                kind: agent
                inputs:
                  - working: 02_write/output/summary.md
                """,
            "02_write": AGENT,
        },
        **{"phases__01_pull__CONTEXT.md": "a", "phases__02_write__CONTEXT.md": "b"},
    )
    _, report = contract.load(flow)
    assert not report.ok
    assert any("does not run before" in line for line in report.errors), report.errors


def test_the_brief_is_a_working_input_like_any_other(tmp_path):
    flow = build(
        tmp_path,
        {"01_pull": f"kind: agent\ninputs:\n  - working: {layout.BRIEF_MD}\noutputs: [a.md]\n"},
        **{"phases__01_pull__CONTEXT.md": "a"},
    )
    _, report = contract.load(flow)
    assert report.ok, report.errors


def test_a_flow_with_no_phases_has_nothing_to_run(tmp_path):
    flow = build(tmp_path, {})
    _, report = contract.load(flow)
    assert not report.ok
    assert any("nothing to run" in line for line in report.errors), report.errors


def test_declaring_no_outputs_warns_and_does_not_refuse(tmp_path):
    flow = build(tmp_path, {"01_pull": "kind: agent\n"}, **{"phases__01_pull__CONTEXT.md": "a"})
    _, report = contract.load(flow)
    assert report.ok, report.errors
    assert any("hands nothing" in line for line in report.warnings), report.warnings


# --- a Run's state -----------------------------------------------------------


def test_a_run_begins_at_phase_one_with_everything_pending():
    run = state.begin("r1", "weekly", ["01_pull", "02_write"])
    assert run.status == "running"
    assert run.current == "01_pull"
    assert [p.status for p in run.phases] == ["pending", "pending"]


def test_run_state_survives_a_round_trip(tmp_path):
    run = state.begin("r1", "weekly", ["01_pull", "02_write"])
    run.status, run.waiting = "paused", "gate"
    state.write(tmp_path, run)
    assert (tmp_path / layout.RUN_JSON).is_file()
    read_back = state.read(tmp_path)
    assert read_back is not None
    assert (read_back.waiting, read_back.current) == ("gate", "01_pull")


def test_an_unreadable_run_folder_reads_as_none_rather_than_raising(tmp_path):
    assert state.read(tmp_path) is None
    (tmp_path / layout.RUN_JSON).write_text("{not json")
    assert state.read(tmp_path) is None


def test_re_running_a_phase_makes_only_later_done_phases_stale():
    run = state.begin("r1", "weekly", ["01_pull", "02_shape", "03_write"])
    for phase in run.phases:
        phase.status = "done"
    changed = state.mark_stale_after(run, "01_pull")
    assert changed == ["02_shape", "03_write"]
    assert run.phase("01_pull").status == "done", "the Phase re-run is not itself stale"
    assert [p.status for p in run.phases] == ["done", "stale", "stale"]


def test_stale_does_not_touch_a_phase_that_produced_nothing():
    run = state.begin("r1", "weekly", ["01_pull", "02_shape", "03_write"])
    run.phase("01_pull").status = "done"
    run.phase("02_shape").status = "failed"
    changed = state.mark_stale_after(run, "01_pull")
    assert changed == []
    assert [p.status for p in run.phases] == ["done", "failed", "pending"]


# --- the event log ------------------------------------------------------------


async def test_a_runs_events_are_a_jsonl_file_and_not_a_row(tmp_path):
    from analyst_agent.core.events import Event
    from analyst_agent.flows import events as flow_events

    append = flow_events.sink(tmp_path)
    await append("01_pull", Event(type="user", data={"text": "go"}))
    await append("01_pull", Event(type="tool_start", data={"tool": "run_query"}))

    lines = flow_events.read(tmp_path)
    assert [line["type"] for line in lines] == ["user", "tool_start"]
    assert [line["seq"] for line in lines] == [1, 2], "seq counted from the file, not memory"
    assert all(line["turn_id"] == "01_pull" for line in lines)


async def test_a_half_written_line_does_not_lose_the_history_above_it(tmp_path):
    """A reader can arrive while a Phase is appending. The Run's history is worth more than
    strictness about its last byte."""
    from analyst_agent.core.events import Event
    from analyst_agent.flows import events as flow_events

    await flow_events.sink(tmp_path)("01_pull", Event(type="user", data={"text": "go"}))
    with (tmp_path / layout.EVENTS_JSONL).open("a") as handle:
        handle.write('{"type": "tool_st')
    assert [line["type"] for line in flow_events.read(tmp_path)] == ["user"]


# --- the Phase's prompt -------------------------------------------------------


def phase_of(dir_name: str, spec: str, prose: str) -> contract.Phase:
    parsed = contract.PhaseSpec.model_validate(__import__("yaml").safe_load(spec))
    return contract.Phase(1, dir_name, Path(dir_name), parsed, prose)


def flow_of() -> contract.Flow:
    return contract.Flow(
        "weekly", Path("weekly"), contract.FlowSpec(title="Weekly"), "Revenue, weekly."
    )


def test_reference_material_is_inlined_and_tabular_working_material_is_not():
    from analyst_agent.flows import prompt

    phase = phase_of("02_write", "kind: agent\noutputs: [summary.md]\n", "Write it up.")
    material = prompt.Material(
        references=[("_config/voice.md", "Plain sentences. No adjectives.")],
        working_text=[("brief.md", "Focus on the EU region.")],
        working_shapes=["### revenue.csv — 2,100 rows x 3 columns"],
    )
    built = prompt.build(flow_of(), phase, material)

    assert "No adjectives." in built, "a rule half-quoted is a rule changed"
    assert "Focus on the EU region." in built
    assert "### revenue.csv" in built
    # This phase asked for no bundles, so it is told the shape is all it gets — see
    # `test_a_phase_that_cannot_run_code_is_not_told_to_read_the_file`.
    assert "cannot open the files" in built
    assert "summary.md" in built
    assert "Write it up." in built
    assert "Revenue, weekly." in built


def test_an_empty_section_says_so_rather_than_being_omitted():
    """Absence is information: a Phase with no reference material has no rules to follow, which
    is different from rules that went missing on the way to the prompt."""
    from analyst_agent.flows import prompt

    phase = phase_of("01_pull", "kind: agent\n", "Pull it.")
    built = prompt.build(flow_of(), phase, prompt.Material())
    assert "no reference material" in built
    assert "from its instructions alone" in built
    assert "declares no outputs" in built


def test_a_cut_reference_file_is_declared_in_the_prompt():
    from analyst_agent.flows import prompt

    truncated: list[str] = []
    text = prompt.clip("x" * (prompt.INLINE_LIMIT + 500), "_config/huge.md", truncated)
    assert len(text) == prompt.INLINE_LIMIT
    assert truncated == ["_config/huge.md"]

    phase = phase_of("01_pull", "kind: agent\n", "Pull.")
    built = prompt.build(
        flow_of(),
        phase,
        prompt.Material(references=[("_config/huge.md", text)], truncated=truncated),
    )
    assert "_config/huge.md was longer" in built


# --- write_output -------------------------------------------------------------


async def test_write_output_refuses_a_filename_the_phase_did_not_declare(tmp_path):
    """The declaration is the contract the next Phase's inputs were written against. A near-miss
    filename would leave the Phase looking successful and the Phase after it reading nothing."""
    from analyst_agent.agent.session import Session
    from analyst_agent.flows import tools

    phase = phase_of("02_write", "kind: agent\noutputs: [summary.md]\n", "Write.")
    session = Session(catalog=None, client=None)
    built = tools.build_phase_tools(session, phase, tmp_path)
    write_output = next(t for t in built if t.name == "write_output")

    said = await write_output.ainvoke({"filename": "summary.markdown", "text": "hi"})
    assert "not one of this Phase's declared outputs" in said
    assert "summary.md" in said
    assert not (tmp_path / "summary.markdown").exists()

    said = await write_output.ainvoke({"filename": "summary.md", "text": "The number went up."})
    assert "Wrote summary.md" in said
    assert (tmp_path / "summary.md").read_text() == "The number went up."


def test_a_phase_gets_only_the_bundles_it_asked_for():
    from analyst_agent.agent.session import Session
    from analyst_agent.flows import tools

    session = Session(catalog=None, client=None)
    prose_only = phase_of("02_write", "kind: agent\noutputs: [a.md]\n", "Write.")
    assert [t.name for t in tools.build_phase_tools(session, prose_only, Path("out"))] == [
        "write_output"
    ], "write_output is in no Bundle and every agent Phase has it"

    with_python = phase_of("01_pull", "kind: agent\ntools: [python]\noutputs: [a.md]\n", "Pull.")
    named = {t.name for t in tools.build_phase_tools(session, with_python, Path("out"))}
    assert named == {"write_output", "run_python", "export_result"}
    assert "run_query" not in named, "a Phase that did not ask for query cannot reach the warehouse"


# --- one Run at a time -------------------------------------------------------


def test_a_second_run_of_one_flow_is_refused_while_the_first_holds_the_lock(tmp_path):
    from analyst_agent.flows import runner

    with runner.hold(tmp_path):
        try:
            with runner.hold(tmp_path):
                raise AssertionError("two runs of one flow held the lock at once")
        except runner.Busy:
            pass
    # Released, so the next Run proceeds.
    with runner.hold(tmp_path):
        pass


def test_begin_refuses_a_broken_flow_before_making_a_run_folder(tmp_path, monkeypatch):
    from analyst_agent.core.config import settings
    from analyst_agent.flows import runner

    monkeypatch.setattr(settings, "flows_dir", str(tmp_path))
    build(tmp_path, {"01_pull": AGENT})  # no CONTEXT.md, so the agent Phase cannot run

    run, report = runner.begin("weekly", "this week")
    assert run is None
    assert not report.ok
    assert not (tmp_path / "weekly" / layout.RUNS).exists(), "no folder for a Flow that cannot run"


def test_begin_writes_the_brief_and_a_run_at_phase_one(tmp_path, monkeypatch):
    from analyst_agent.core.config import settings
    from analyst_agent.flows import runner

    monkeypatch.setattr(settings, "flows_dir", str(tmp_path))
    build(
        tmp_path,
        {"01_pull": AGENT, "02_write": AGENT},
        **{"phases__01_pull__CONTEXT.md": "a", "phases__02_write__CONTEXT.md": "b"},
    )

    run, report = runner.begin("weekly", "Focus on the EU region.")
    assert report.ok, report.errors
    assert run is not None and run.current == "01_pull"

    run_folder = tmp_path / "weekly" / layout.RUNS / run.id
    assert (run_folder / layout.BRIEF_MD).read_text() == "Focus on the EU region."
    assert state.read(run_folder).current == "01_pull"
    assert runner.flows() == ["weekly"]


# --- what a definition file is ------------------------------------------------


def test_only_the_definition_is_writable_and_a_runs_path_never_is():
    """The line between a Flow's definition and the record of what ran. A Run's artifacts *are*
    editable, but at a Gate and through the route that knows which Phase is paused — not as a
    file in the Flow, which would let a Phase's output be rewritten while a later Phase read it.
    """
    assert layout.writable("flow.yml") == ""
    assert layout.writable("CONTEXT.md") == ""
    assert layout.writable("_config/voice.md") == ""
    assert layout.writable("phases/01_pull/phase.yml") == ""
    assert layout.writable("phases/01_pull/CONTEXT.md") == ""
    assert layout.writable("phases/01_pull/fetch.py") == ""
    assert layout.writable("phases/01_pull/references/structure.md") == ""

    assert "record of what ran" in layout.writable("runs/r1/01_pull/output/a.md")
    assert "record of what ran" in layout.writable("chat.json")
    assert "record of what ran" in layout.writable("run.lock")


def test_a_path_that_is_not_part_of_a_flow_says_which_shape_it_wanted():
    assert "'.' or '..'" in layout.writable("phases/01_pull/../../etc/passwd")
    assert "not one of the flow's own files" in layout.writable("notes.md")
    assert "is not part of a flow" in layout.writable("output/a.md")
    assert "NN_name" in layout.writable("phases/pull/phase.yml")
    assert "is a folder, not a file" in layout.writable("phases/01_pull")
    assert "not something a phase holds" in layout.writable("phases/01_pull/notes.txt")
    assert layout.writable("") != ""
    assert layout.writable("/etc/passwd") != ""


# --- which artifact may be edited, and when -----------------------------------


def gated(current: str, statuses: dict[str, str]) -> state.RunState:
    run = state.begin("r1", "weekly", list(statuses))
    for name, status in statuses.items():
        run.phase(name).status = status
    run.current, run.status, run.waiting = current, "paused", "gate"
    return run


def test_only_the_phase_at_the_gate_may_be_edited():
    from analyst_agent.api.flows import controller

    run = gated("02_write", {"01_pull": "done", "02_write": "pending"})
    assert controller.editable_phase(run) == "01_pull"

    run.waiting = ""
    run.status = "running"
    assert controller.editable_phase(run) == "", "nothing is editable while a phase is running"

    done = state.begin("r1", "weekly", ["01_pull"])
    done.status = "done"
    assert controller.editable_phase(done) == "", "a finished run has no gate"


def test_editing_an_artifact_is_refused_unless_the_run_is_paused_at_that_phase(tmp_path):
    from fastapi import HTTPException

    from analyst_agent.api.flows import controller

    run = gated("02_write", {"01_pull": "done", "02_write": "pending"})
    target = controller.artifact(tmp_path, run, "01_pull/output/summary.md", writing=True)
    assert target == tmp_path / "01_pull" / "output" / "summary.md"

    for path, expected in [
        # Two different mistakes, so two different refusals — a later phase has not run, an
        # earlier one has already been read. The first live test of this said "already been read"
        # about a phase that had not run yet, which sends a person looking in the wrong place.
        ("02_write/output/a.md", "has not run yet"),
        ("01_pull/scratch/chart.png", "not a phase's output"),
        ("../../etc/passwd", "outside the run folder"),
    ]:
        try:
            controller.artifact(tmp_path, run, path, writing=True)
            raise AssertionError(f"{path} was allowed")
        except HTTPException as exc:
            assert expected in str(exc.detail), (path, exc.detail)

    three = gated("03_ship", {"01_pull": "done", "02_write": "done", "03_ship": "pending"})
    try:
        controller.artifact(tmp_path, three, "01_pull/output/summary.md", writing=True)
        raise AssertionError("edited a phase a later one had already read")
    except HTTPException as exc:
        assert "already been read by the phase after it" in str(exc.detail), exc.detail

    running = gated("02_write", {"01_pull": "done", "02_write": "pending"})
    running.waiting, running.status = "", "running"
    try:
        controller.artifact(tmp_path, running, "01_pull/output/summary.md", writing=True)
        raise AssertionError("edited an artifact while a phase was running")
    except HTTPException as exc:
        assert "not paused at a gate" in str(exc.detail)


def test_the_latest_symlink_does_not_make_a_run_appear_twice(tmp_path, monkeypatch):
    """`runs/latest` points at a real Run directory, so anything counting `is_dir()` folders finds
    the newest Run under both names. Found by a route test asserting a list of one."""
    from analyst_agent.core.config import settings
    from analyst_agent.flows import runner

    monkeypatch.setattr(settings, "flows_dir", str(tmp_path))
    build(tmp_path, {"01_pull": AGENT}, **{"phases__01_pull__CONTEXT.md": "Pull."})

    first, report = runner.begin("weekly", "one")
    assert report.ok, report.errors
    flow = tmp_path / "weekly"
    assert (flow / layout.RUNS / layout.LATEST).is_symlink()
    assert layout.run_ids(flow) == [first.id]


def test_a_phase_that_cannot_run_code_is_not_told_to_read_the_file():
    """Found on the first real run. A phase with a tabular working input and no `python` bundle
    was told to "read them with run_python" — an instruction it has no tool for, and a model given
    an impossible instruction works around it silently."""
    from analyst_agent.flows import prompt

    material = prompt.Material(working_shapes=["### revenue.csv — 2,100 rows x 3 columns"])
    with_python = phase_of("02_x", "kind: agent\ntools: [python]\noutputs: [a.md]\n", "Do it.")
    assert "read them with `run_python`" in prompt.build(flow_of(), with_python, material)

    prose_only = phase_of("02_x", "kind: agent\noutputs: [a.md]\n", "Do it.")
    built = prompt.build(flow_of(), prose_only, material)
    assert "cannot open the files" in built
    assert "run_python" not in built, "a phase without the bundle must not be sent to the tool"


async def test_write_output_refuses_an_empty_declared_output(tmp_path):
    """A Phase that writes an empty declared output looks like it succeeded and produced nothing.
    Seen live: told not to re-type a CSV a tool had already made, the model called `write_output`
    with an empty string instead of not calling it — and only the sandbox's copy overwriting the
    file kept the artifact correct. Refused here so that rescue is not what the design rests on.
    """
    from analyst_agent.agent.session import Session
    from analyst_agent.flows import tools

    phase = phase_of("01_pull", "kind: agent\noutputs: [revenue.csv]\n", "Pull.")
    built = tools.build_phase_tools(Session(catalog=None, client=None), phase, tmp_path)
    write_output = next(t for t in built if t.name == "write_output")

    for empty in ("", "   \n\t"):
        said = await write_output.ainvoke({"filename": "revenue.csv", "text": empty})
        assert "would be empty" in said
        assert "do not call write_output for it at all" in said
        assert not (tmp_path / "revenue.csv").exists()
