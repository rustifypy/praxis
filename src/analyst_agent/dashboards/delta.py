"""Change since the previous refresh, by subtracting two stored runs — `CubeQuery` cannot
express a period comparison. Single-row results only.
"""

from dataclasses import dataclass

from ..cube.shape import Column
from .store import TileResult


@dataclass
class Delta:
    column: str
    label: str
    value: float
    previous: float
    change: float
    percent: float | None
    """`None` when the previous value was zero — ∞ and 100% would both be lies. The absolute
    change is still correct and is what a tile shows instead."""


def deltas(
    rows: list[dict] | None, previous: TileResult | None, columns: list[Column]
) -> tuple[list[Delta], float | None]:
    """Per-measure change, and when the previous refresh ran. Empty for every shape this cannot
    speak about: no previous run, pruned rows, more than one row either side, a non-number."""
    if previous is None or previous.rows is None or rows is None:
        return [], None
    if len(rows) != 1 or len(previous.rows) != 1:
        return [], None

    now, before = rows[0], previous.rows[0]
    out: list[Delta] = []
    for column in columns:
        if column.kind != "measure":
            continue
        try:
            value = float(now[column.id])
            was = float(before[column.id])
        except (KeyError, TypeError, ValueError):
            # A measure absent from one side means the query changed between refreshes, which
            # makes the pair incomparable rather than zero.
            continue
        out.append(
            Delta(
                column=column.id,
                label=column.label,
                value=value,
                previous=was,
                change=value - was,
                percent=None if was == 0 else (value - was) / abs(was) * 100,
            )
        )
    return out, previous.created_at
