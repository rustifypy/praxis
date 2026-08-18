"""The paragraph above a refreshed dashboard and the caption under each tile: **one** model
call for the whole board, regenerated every refresh and never carried forward. A failure
returns nothing rather than raising.
"""

import json

from pydantic import BaseModel, Field

from ..core.llm import build_model
from ..cube.shape import Column
from .delta import Delta
from .tiles import insight_slice


class TileNote(BaseModel):
    tile_id: str
    caption: str


class BoardInsight(BaseModel):
    summary: str
    tiles: list[TileNote] = Field(default_factory=list)


PROMPT = """You are writing the summary paragraph above a dashboard someone has just \
refreshed, and a one-line caption under each of its tiles. Below is every tile on it: its \
id, its title, its columns, and its rows.

Rules for the summary:
- Two to four sentences. No headings, no bullet lists, no markdown table.
- Say what the numbers show *together*. The reader can see each tile; what they cannot see
  is the relationship between them. Prefer "revenue rose while order count held flat" over
  restating either tile.

Rules for each caption:
- Set `tile_id` to the id given for that tile, copied exactly. **Never write the id, or the
  word "tile", in the caption text** — a caption is printed under the tile it belongs to and
  has nothing to name.
- One short sentence about that tile alone. Say what the reader would miss glancing at it —
  the shape, the outlier, the concentration. Never restate the tile's title.
- A tile you have nothing worth saying about is **left out of the list**. An obvious caption
  is worse than none, because every caption after it stops being worth reading.

Rules for both:
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
    tiles: list[tuple[str, str, list[Column], list[dict], list[Delta], float | None]],
) -> tuple[str, dict[str, str]]:
    """The board's paragraph and a caption per tile id, from one call — the rows are already
    in this prompt, and a call per tile is twelve for a weaker result.

    `tiles` is (id, title, columns, rows, deltas, previous-run timestamp) per tile.
    """
    blocks = []
    known = set()
    for tile_id, tile_title, columns, rows, changes, since in tiles:
        if not rows:
            continue
        known.add(tile_id)
        sample = insight_slice(columns, rows)
        block = (
            f"## {tile_title}\ntile_id: {tile_id}\n"
            f"columns: {', '.join(f'{c.label} ({c.kind})' for c in columns)}\n"
        )
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
        return "", {}
    prompt = PROMPT + f"# {title}\n\n" + "\n\n".join(blocks)
    try:
        reply = (
            await build_model()
            .with_structured_output(BoardInsight)
            .ainvoke([{"role": "user", "content": prompt}])
        )
    except Exception:  # noqa: BLE001 — a refresh that stored its rows has succeeded
        return "", {}
    insight = BoardInsight.model_validate(reply)
    # Filtered against the ids that went in: a caption for a tile that is not on this board
    # would be written to nothing, and one hallucinated id must not drop the rest.
    captions = {n.tile_id: n.caption.strip() for n in insight.tiles if n.tile_id in known}
    return insight.summary.strip(), {k: v for k, v in captions.items() if v}
