"""The paragraph above a refreshed dashboard: one model call for the whole board, regenerated
every refresh and never carried forward. A failure returns "" rather than raising.
"""

import json

from ..core.llm import build_model, text_of
from ..cube.shape import Column
from .delta import Delta
from .tiles import insight_slice

PROMPT = """You are writing the summary paragraph above a dashboard someone has just \
refreshed. Below is every tile on it: its title, its columns, and its rows.

Rules:
- Two to four sentences. No headings, no bullet lists, no markdown table.
- Say what the numbers show *together*. The reader can see each tile; what they cannot see
  is the relationship between them. Prefer "revenue rose while order count held flat" over
  restating either tile.
- Every figure you quote must appear in the data below. Never estimate or interpolate.
- Percentage changes are given to you under "change since the last refresh". Use those
  numbers as written and do not compute your own. If none are given, do not describe change.
- A row sample marked as sampled is spread across the whole series, not the first rows.
  Do not describe it as the beginning or the end of anything.
- If the data does not support anything worth saying, say only what it shows. Do not invent
  significance.

"""


async def summarise(
    title: str,
    tiles: list[tuple[str, list[Column], list[dict], list[Delta], float | None]],
) -> str:
    """`tiles` is (title, columns, rows, deltas, previous-run timestamp) per tile."""
    blocks = []
    for tile_title, columns, rows, changes, since in tiles:
        if not rows:
            continue
        sample = insight_slice(columns, rows)
        block = f"## {tile_title}\ncolumns: {', '.join(f'{c.label} ({c.kind})' for c in columns)}\n"
        if len(sample) < len(rows):
            block += f"rows: {len(rows)}, sampled to {len(sample)} across the whole series\n"
        else:
            block += f"rows: {len(rows)}\n"
        if changes:
            block += "change since the last refresh"
            if since is not None:
                block += f" (which ran at epoch {since:.0f})"
            block += ":\n"
            for change in changes:
                percent = "n/a (was zero)" if change.percent is None else f"{change.percent:+.1f}%"
                block += f"- {change.label}: {change.value} was {change.previous} ({percent})\n"
        blocks.append(block + json.dumps(sample, indent=2, default=str))

    if not blocks:
        return ""
    prompt = PROMPT + f"# {title}\n\n" + "\n\n".join(blocks)
    try:
        reply = await build_model().ainvoke([{"role": "user", "content": prompt}])
    except Exception:  # noqa: BLE001 — a refresh that stored its rows has succeeded
        return ""
    return text_of(reply.content).strip()
