from dataclasses import dataclass

from ...core.boards import TileResult
from ...modelstore.shape import Column


@dataclass
class Delta:
    column: str
    label: str
    value: float
    previous: float
    change: float
    # None when the previous value was zero: ∞ and 100% would both be lies, so a tile shows
    # the absolute change instead.
    percent: float | None


def deltas(
    rows: list[dict] | None, previous: TileResult | None, columns: list[Column]
) -> tuple[list[Delta], float | None]:
    if previous is None or previous.rows is None or rows is None:
        return [], None
    if len(rows) != 1 or len(previous.rows) != 1:
        return [], None

    latest, earlier = rows[0], previous.rows[0]
    changes: list[Delta] = []
    for column in columns:
        if column.kind != "measure":
            continue
        try:
            value = float(latest[column.id])
            was = float(earlier[column.id])
        except (KeyError, TypeError, ValueError):
            # A measure absent from one side means the query changed between refreshes, which
            # makes the pair incomparable rather than zero.
            continue
        changes.append(
            Delta(
                column=column.id,
                label=column.label,
                value=value,
                previous=was,
                change=value - was,
                percent=None if was == 0 else (value - was) / abs(was) * 100,
            )
        )
    return changes, previous.created_at
