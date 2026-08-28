"""What an agent Phase can do: its **Bundles**, plus `write_output`.

A Phase names Bundles and never tools, so a tool can be renamed without breaking a file somebody
wrote. `BUNDLES` in `contract` is the closed menu; this is where each one turns into the toolset
the query agent already has, unchanged — a Phase asking for `query` gets exactly what a chat gets,
because two spellings of `run_query` would be two things to keep true about the gate.

`write_output` is in no Bundle. Every agent Phase has it, because producing the outputs it
declared is what being an agent Phase *means* — and because it is the only way a Phase with no
`python` Bundle can write a file at all. It writes to portal disk directly rather than through the
sandbox, so a Phase whose job is three paragraphs of prose needs no sandbox to be up.
"""

from langchain.tools import tool

from ..agent.session import Session
from ..agent.tools.code import build_code_tools
from ..agent.tools.query import build_query_tools
from ..agent.tools.trace import end, start
from .contract import Phase


def build_phase_tools(session: Session, phase: Phase, output) -> list:
    """Every tool this Phase may call.

    `output` is the Phase's `output/` inside the Run — passed in already resolved, because
    deciding where a Phase's output goes is the runner's job and re-deciding it here is how two
    answers to that question would appear.
    """
    tools = [_write_output(phase, output)]
    if "query" in phase.spec.tools:
        tools.extend(build_query_tools(session))
    if "python" in phase.spec.tools:
        tools.extend(build_code_tools(session))
    return tools


def _write_output(phase: Phase, output):
    declared = list(phase.spec.outputs)

    @tool
    async def write_output(filename: str, text: str) -> str:
        """Write one of this Phase's declared outputs. `filename` must be exactly as declared.

        This is how a Phase produces its result. The file lands in the Phase's output folder,
        where the person reviewing this Run reads it and where the next Phase looks for it.
        """
        start("write_output", filename)
        # Refused rather than accepted-and-put-somewhere, because the declaration is the contract
        # the next Phase's inputs were written against. A near-miss filename would leave the Phase
        # looking successful and the Phase after it reading nothing.
        if filename not in declared:
            end("write_output", False, errors=[f"{filename} is not declared by this Phase"])
            listed = ", ".join(declared) or "(this Phase declares no outputs)"
            return (
                f"{filename} is not one of this Phase's declared outputs. Write one of: {listed}. "
                "If a different file is genuinely needed, the Phase's phase.yml has to declare it."
            )
        # An empty declared output is a Phase that produced nothing while looking like it
        # succeeded. Seen live: told not to re-type a CSV `export_result` had already made, the
        # model called this with an empty string rather than not calling it — and only the
        # sandbox's copy overwriting it kept the artifact correct. Refused here so that rescue is
        # not what the design rests on.
        if not text.strip():
            end("write_output", False, errors=[f"{filename} would be empty"])
            return (
                f"{filename} would be empty, so nothing was written. If a tool has already "
                "produced this file, it is collected for you — do not call write_output for it "
                "at all. Otherwise write the content."
            )
        try:
            output.mkdir(parents=True, exist_ok=True)
            (output / filename).write_text(text)
        except OSError as exc:
            end("write_output", False, errors=[str(exc)])
            return f"{filename} could not be written: {exc}"
        end("write_output", True, note=f"{filename}, {len(text)} characters")
        return f"Wrote {filename} ({len(text)} characters)."

    return write_output
