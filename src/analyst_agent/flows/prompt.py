"""What an agent Phase's model actually sees.

Three of the paper's five layers are assembled here, in this order: the portal's own preamble,
the Flow's prose (what this pipeline is for), the Phase's prose (what this step does), then the
material. No agent tool can read a file — there is no `read_file` anywhere in this codebase — so
whatever is not put here is not available, which makes this module the Phase's entire world.

**Reference material is inlined verbatim and Working material is not.** That is the layer
distinction doing mechanical work rather than naming things: Reference is a rule to be
internalised ("write like this"), so the model gets the words; Working material is input to be
transformed, so the model gets prose whole but tabular data as a *shape* — columns, distinct
counts, a few rows — with the bytes in the sandbox for `run_python`. Inlining a 200,000-row CSV
would spend the context window on the one thing the sandbox exists to handle.

This module reads nothing and calls nothing. The runner gathers the material and this renders it,
which is what lets the assembly be tested without a store, a sandbox or a model.
"""

from dataclasses import dataclass, field

from .contract import Flow, Phase

# What one Reference file may contribute. A voice guide is a page; something the size of a novel
# in `_config/` is a mistake worth naming rather than silently spending the window on.
INLINE_LIMIT = 20_000

PREAMBLE = """You are running one Phase of a Flow: a numbered step in a pipeline someone \
repeats, with a person reading your output before the next step runs.

- Do exactly this Phase's job and stop. The steps before it have run; the ones after it are not
  yours. Do not do the next Phase's work because you can see what it will need.
- Everything you are given is below. There is no file you can open that is not here, and no
  earlier conversation to remember — a Phase is a scoped task, not a chat.
- Produce every declared output with `write_output`, using exactly the filenames listed. A Phase
  that says nothing and writes nothing has failed, however good its reasoning was.
- **One exception: a file `export_result` or a script already wrote is already an output.** Do not
  call `write_output` for it at all — not with its contents re-typed, and not with an empty
  string. The real file is collected for you.
- Reference material is a constraint: follow it. Working material is input: transform it.
- Write for the person who opens the file next, not for a chat window. No preamble, no "here is
  your summary" — the file is the answer.
"""


@dataclass
class Material:
    """The Phase's inputs, already read. Paths are as declared, so an error names what the author
    wrote rather than an absolute path they never typed."""

    # (path, text) — inlined whole, because a rule half-quoted is a rule changed.
    references: list[tuple[str, str]] = field(default_factory=list)
    # (path, text) — prose from an earlier Phase, or the Brief.
    working_text: list[tuple[str, str]] = field(default_factory=list)
    # `Profile.as_prompt()` for each tabular Working input: shape, not contents.
    working_shapes: list[str] = field(default_factory=list)
    # Anything cut at `INLINE_LIMIT`. Said out loud in the prompt: a model reasoning from half a
    # voice guide should know it has half.
    truncated: list[str] = field(default_factory=list)


def clip(text: str, path: str, truncated: list[str]) -> str:
    if len(text) <= INLINE_LIMIT:
        return text
    truncated.append(path)
    return text[:INLINE_LIMIT]


def build(flow: Flow, phase: Phase, material: Material) -> str:
    """The Phase's system prompt.

    Sections appear even when empty of material, because their absence is information: a Phase
    with no Reference material is one with no rules to follow, and saying so beats leaving the
    model to wonder whether they were omitted by accident.
    """
    parts = [PREAMBLE, f"# Flow: {flow.spec.title}"]
    if flow.spec.description:
        parts.append(flow.spec.description)
    if flow.prose.strip():
        parts.append(flow.prose.strip())

    parts.append(f"# This Phase: {phase.dir_name}")
    parts.append(phase.prose.strip() or "(this Phase's CONTEXT.md is empty)")

    parts.append("# Reference material — rules to follow")
    if material.references:
        for path, text in material.references:
            parts.append(f"## {path}\n\n{text.strip()}")
    else:
        parts.append("(none — this Phase has no reference material)")

    parts.append("# Working material — input to transform")
    if material.working_text or material.working_shapes:
        for path, text in material.working_text:
            parts.append(f"## {path}\n\n{text.strip()}")
        for shape in material.working_shapes:
            parts.append(shape)
        if material.working_shapes:
            # What follows depends on whether this Phase can open the file at all. Telling a
            # Phase without the `python` Bundle to "read it with run_python" is an instruction it
            # cannot follow, and a model given an impossible instruction invents its way around
            # it — on the first real run, one summarised a CSV from the head rows of its shape
            # without ever saying that is what it had done.
            if "python" in phase.spec.tools:
                parts.append(
                    "Those are shapes, not contents. The files are in the working directory — "
                    "read them with `run_python` when you need the rows themselves."
                )
            else:
                parts.append(
                    "Those are shapes, not contents, and this Phase cannot open the files: it "
                    "has no way to run code. Work from the columns and the few rows shown, and "
                    "say so if the question needs more than they can answer."
                )
    else:
        parts.append("(none — this Phase produces its output from its instructions alone)")

    parts.append("# Outputs you must write")
    if phase.spec.outputs:
        listed = "\n".join(f"- {name}" for name in phase.spec.outputs)
        parts.append(f"{listed}\n\nWrite each with `write_output`. The Phase fails without them.")
    else:
        parts.append("(this Phase declares no outputs, so nothing is required of `write_output`)")

    if material.truncated:
        cut = ", ".join(material.truncated)
        parts.append(
            f"# Note\n\n{cut} was longer than this Phase can carry and has been cut at "
            f"{INLINE_LIMIT:,} characters. Say so in your output if it mattered."
        )

    return "\n\n".join(parts)
