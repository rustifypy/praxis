"""The dashboard tools. **Nothing here refreshes** — that is a button on the page, so a chat
turn cannot become two dozen warehouse queries, and a new tile has no data until it is.
"""

import json
from dataclasses import dataclass

from langchain.tools import tool

from ...core.events import Event, emit
from ...cube.catalog import Catalog
from ...dashboards import layout as layouts
from ...dashboards import tiles as tilecheck
from ...dashboards.store import DashboardStore

EVENT_KEYS = frozenset({"tool", "label", "ok", "note", "errors"})
"""Every key a dashboard tool's event may carry — asserted by a test. Absent on purpose:
anything holding state (`layout`, `spec`, `query`, `tiles`)."""


@dataclass
class DashboardContext:
    """Separate from the query `Session`: a different store, and the query tools never read it."""

    store: DashboardStore
    catalog: Catalog


def _start(tool_name: str, label: str) -> None:
    emit(Event(type="tool_start", data={"tool": tool_name, "label": label}))


def _end(tool_name: str, ok: bool, note: str = "", errors: list[str] | None = None) -> None:
    data: dict = {"tool": tool_name, "ok": ok}
    if note:
        data["note"] = note
    if errors:
        data["errors"] = errors
    emit(Event(type="tool_end", data=data))


def build_dashboard_tools(ctx: DashboardContext) -> list:
    @tool
    async def list_dashboards() -> str:
        """List the saved dashboards with their ids. Call this before any other dashboard
        tool — every one of them takes an id, and ids are not guessable."""
        _start("list_dashboards", "Reading the saved dashboards")
        boards = await ctx.store.list_dashboards()
        _end("list_dashboards", True, note=f"{len(boards)} saved")
        if not boards:
            return "No dashboards yet. `create_dashboard` makes one."
        lines = []
        for board in boards:
            live = await ctx.store.tiles(board.id)
            last = await ctx.store.latest_run(board.id)
            when = "never refreshed" if last is None else f"last refreshed {last.started_at:.0f}"
            lines.append(f"- `{board.id}` {board.title!r} — {len(live)} tiles, {when}")
        return "\n".join(lines)

    @tool
    async def read_dashboard(dashboard_id: str) -> str:
        """Show one dashboard's tiles — ids, titles, stored queries and charts — and its
        current layout. Call this before editing anything on it."""
        _start("read_dashboard", "Reading a dashboard")
        board = await ctx.store.get(dashboard_id)
        if board is None:
            _end("read_dashboard", False)
            return f"No dashboard with id {dashboard_id!r}. Call list_dashboards."
        live = await ctx.store.tiles(dashboard_id)
        current = await ctx.store.layout(dashboard_id)
        _end("read_dashboard", True, note=f"{board.title} — {len(live)} tiles")

        out = [f"# {board.title}"]
        for tile in live:
            out.append(
                f"\n## `{tile.id}` {tile.title!r}\n"
                f"chart: {json.dumps(tile.chart)}\n"
                f"query: {json.dumps(tile.query)}"
            )
        if current:
            out.append(f"\n## layout (version {current['version']})\n{json.dumps(current['spec'])}")
        else:
            out.append("\n## layout\nNone saved — tiles render in creation order.")
        return "\n".join(out)

    @tool
    async def create_dashboard(title: str) -> str:
        """Create an empty dashboard. Add tiles to it with `add_tile`."""
        _start("create_dashboard", f"Creating {title!r}")
        board = await ctx.store.create(title)
        _end("create_dashboard", True, note=title)
        return f"Created {title!r} with id `{board.id}`. It has no tiles yet."

    @tool
    async def add_tile(dashboard_id: str, title: str, query: dict, chart: dict) -> str:
        """Save a query and its chart as a tile. `chart` is {type, x, y, series, target}.
        Types: line, area, stacked_area, bar, stacked_bar, grouped_bar, hbar, donut, scatter,
        heatmap, gauge, sparkline, kpi, stat, table. stat, sparkline, area and stacked_area
        need a time grain on x; stacked and grouped bars and heatmap take a series (heatmap
        requires one, as its second axis); scatter needs a measure on x; gauge takes a target
        and one row. Checked but not executed — a tile shows data after a refresh."""
        _start("add_tile", f"Adding {title!r}")
        board = await ctx.store.get(dashboard_id)
        if board is None:
            _end("add_tile", False)
            return f"No dashboard with id {dashboard_id!r}. Call list_dashboards."

        checked = tilecheck.validate(query, chart, ctx.catalog)
        if not checked.ok:
            # A rejection is text to act on, exactly as the query gate returns.
            _end("add_tile", False, errors=checked.errors)
            return "Tile rejected:\n" + "\n".join(f"- {e}" for e in checked.errors)

        tile = await ctx.store.add_tile(
            dashboard_id, title, query, chart, time_member=checked.time_member
        )
        _end("add_tile", True, note=title)
        out = f"Added {title!r} to {board.title!r} as `{tile.id}`."
        if checked.warnings:
            out += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in checked.warnings)
        return out

    @tool
    async def update_tile(
        tile_id: str, title: str = "", query: dict | None = None, chart: dict | None = None
    ) -> str:
        """Change a tile's title, its stored query, or its chart. Omitted arguments are left
        alone. A changed query or chart is re-checked the same way a new tile is."""
        _start("update_tile", "Editing a tile")
        tile = await ctx.store.tile(tile_id)
        if tile is None:
            _end("update_tile", False)
            return f"No tile with id {tile_id!r}. Call read_dashboard."

        # Validated as the pair it will become: a new chart has to suit the stored query,
        # and a new query has to still support the stored chart.
        time_member = None
        if query is not None or chart is not None:
            checked = tilecheck.validate(
                query if query is not None else tile.query,
                chart if chart is not None else tile.chart,
                ctx.catalog,
            )
            if not checked.ok:
                _end("update_tile", False, errors=checked.errors)
                return "Edit rejected:\n" + "\n".join(f"- {e}" for e in checked.errors)
            time_member = checked.time_member

        await ctx.store.update_tile(
            tile_id,
            title=title or None,
            query=query,
            chart=chart,
            time_member=time_member,
        )
        _end("update_tile", True, note=title or tile.title)
        return f"Updated {title or tile.title!r}."

    @tool
    async def remove_tile(tile_id: str) -> str:
        """Take a tile off a dashboard. Recoverable — the tile is hidden rather than
        destroyed, and reverting a layout that named it brings it back."""
        _start("remove_tile", "Removing a tile")
        tile = await ctx.store.tile(tile_id)
        if tile is None:
            _end("remove_tile", False)
            return f"No tile with id {tile_id!r}. Call read_dashboard."
        await ctx.store.set_tile_deleted(tile_id)
        _end("remove_tile", True, note=tile.title)
        return f"Removed {tile.title!r}. It can be restored by reverting to an earlier layout."

    @tool
    async def set_layout(dashboard_id: str, items: list[dict], note: str, columns: int = 2) -> str:
        """Rearrange a dashboard. `items` is an ordered list of {"tile": id, "span": n},
        `columns` is 1-3, and `note` says in a few words what the change was for. Tiles you
        leave out are appended rather than hidden, so a partial list is safe."""
        _start("set_layout", note or "Rearranging")
        board = await ctx.store.get(dashboard_id)
        if board is None:
            _end("set_layout", False)
            return f"No dashboard with id {dashboard_id!r}. Call list_dashboards."
        live = await ctx.store.tiles(dashboard_id)
        if not live:
            _end("set_layout", False)
            return f"{board.title!r} has no tiles to arrange."

        checked, errors = layouts.validate({"columns": columns, "items": items}, live)
        if checked is None:
            _end("set_layout", False, errors=errors)
            return "Layout rejected:\n" + "\n".join(f"- {e}" for e in errors)

        version = await ctx.store.save_layout(dashboard_id, checked, note=note)
        _end("set_layout", True, note=note)
        placed = len(checked["items"])
        extra = placed - len(items)
        out = f"Saved layout version {version} of {board.title!r} — {placed} tiles."
        if extra > 0:
            out += f" {extra} you did not mention were appended rather than hidden."
        return out

    @tool
    async def revert_layout(dashboard_id: str, version: int) -> str:
        """Go back to an earlier layout. Appended as a new version rather than rewinding, so
        the history never shrinks and a revert is itself undoable."""
        _start("revert_layout", f"Reverting to layout {version}")
        old = await ctx.store.layout(dashboard_id, version=version)
        if old is None:
            _end("revert_layout", False)
            history = await ctx.store.layout_history(dashboard_id)
            available = ", ".join(str(v["version"]) for v in history) or "none"
            return f"No layout version {version}. Saved versions: {available}."

        live = await ctx.store.tiles(dashboard_id)
        # An old version can name a tile deleted since. Dropped rather than treated as an
        # error, because the version was valid when it was written and the alternative is a
        # revert that can never succeed.
        kept = [i for i in old["spec"].get("items", []) if any(t.id == i.get("tile") for t in live)]
        dropped = len(old["spec"].get("items", [])) - len(kept)
        checked, errors = layouts.validate(
            {"columns": old["spec"].get("columns", 2), "items": kept}, live
        )
        if checked is None:
            _end("revert_layout", False, errors=errors)
            return "Revert rejected:\n" + "\n".join(f"- {e}" for e in errors)

        saved = await ctx.store.save_layout(
            dashboard_id, checked, note=f"reverted to version {version}"
        )
        _end("revert_layout", True, note=f"back to version {version}")
        out = f"Reverted to version {version}, saved as version {saved}."
        if dropped:
            out += f" {dropped} tile(s) in that version no longer exist and were left out."
        return out

    return [
        list_dashboards,
        read_dashboard,
        create_dashboard,
        add_tile,
        update_tile,
        remove_tile,
        set_layout,
        revert_layout,
    ]
