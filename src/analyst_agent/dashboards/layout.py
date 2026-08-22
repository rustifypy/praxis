from .store import Tile

# Past three, a tile is too narrow for a time axis with readable ticks: `ResultChart`'s label
# thinning is sized for a full-width block.
MAX_COLUMNS = 3

# A table's columns are its content, so a half-width one is a horizontal scrollbar.
FULL_WIDTH = ("table", "heatmap")

HEADLINE = ("kpi", "stat", "gauge")


def default_layout(tiles: list[Tile], columns: int = 2) -> dict:
    # Headline numbers first — the summary a dashboard is opened for. Everything else keeps
    # creation order, which is the order the tiles were thought of.
    headline = [tile for tile in tiles if tile.chart.get("type") in HEADLINE]
    rest = [tile for tile in tiles if tile.chart.get("type") not in HEADLINE]
    return {
        "columns": columns,
        "items": [
            {
                "tile": tile.id,
                "span": columns if tile.chart.get("type") in FULL_WIDTH else 1,
            }
            for tile in [*headline, *rest]
        ],
    }


def validate(spec: dict, tiles: list[Tile]) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    by_id = {tile.id: tile for tile in tiles}

    columns = spec.get("columns", 2)
    if not isinstance(columns, int) or not 1 <= columns <= MAX_COLUMNS:
        errors.append(f"columns must be a whole number from 1 to {MAX_COLUMNS} — got {columns!r}")
        columns = 2

    raw_items = spec.get("items")
    if not isinstance(raw_items, list):
        return None, [*errors, "items must be a list of {tile, span} objects"]

    items: list[dict] = []
    placed: set[str] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            errors.append(f"each item must be an object with a tile id — got {entry!r}")
            continue
        tile_id = str(entry.get("tile", ""))
        if tile_id not in by_id:
            errors.append(f"tile {tile_id!r} is not on this dashboard")
            continue
        if tile_id in placed:
            errors.append(f"tile {tile_id!r} is placed twice")
            continue
        span = entry.get("span", 1)
        if not isinstance(span, int) or not 1 <= span <= columns:
            errors.append(
                f"span for {by_id[tile_id].title!r} must be from 1 to {columns} — got {span!r}"
            )
            continue
        placed.add(tile_id)
        items.append({"tile": tile_id, "span": span})

    if errors:
        return None, errors

    # Appended in creation order, so an unmentioned tile lands where it would have been.
    for tile in tiles:
        if tile.id not in placed:
            items.append(
                {"tile": tile.id, "span": columns if tile.chart.get("type") in FULL_WIDTH else 1}
            )

    if not items:
        return None, ["a layout needs at least one tile"]
    return {"columns": columns, "items": items}, []
