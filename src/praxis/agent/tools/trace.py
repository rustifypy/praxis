"""The two events every tool authors: what it is doing, and how it ended.

One pair for all four toolsets. The dashboard and editor tools carried a byte-identical copy
of it each, and the query tools wrote `emit(Event(...))` out at every call site — three
spellings of one rule, which is how the shape a browser matches on drifts.

What a tool may put in `data` beyond `tool`/`label`/`ok` is per-toolset and asserted there by
`EVENT_KEYS`; this only decides the frame.
"""

from ...core.events import Event, emit


def start(tool: str, label: str, **data: object) -> None:
    emit(Event(type="tool_start", data={"tool": tool, "label": label, **_kept(data)}))


def end(tool: str, ok: bool, **data: object) -> None:
    emit(Event(type="tool_end", data={"tool": tool, "ok": ok, **_kept(data)}))


def _kept(data: dict) -> dict:
    # An absent note is no key rather than an empty one, so a caller can pass a conditional
    # without the event growing a field that says nothing. Compared against the empties rather
    # than tested for truth: `exit_code=0` and `ok=False` are answers.
    return {key: value for key, value in data.items() if value not in (None, "", [], {})}
