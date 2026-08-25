"""Boards: the grid, its tiles, its layout history, and the only Refresh.

Three invariants, none of them local to one function here:

- **The row is the truth and a tool event carries only intent.** Undo comes from `layouts` being
  append-only, not from state carried on an event.
- **A refresh is atomic.** Every tile compiles before any executes, and the same pass runs on
  open — `checks` and `refresh` share `_compile_all` so the banner and the button cannot
  disagree.
- **A refresh re-executes, never rewrites.** Whether a window moves is a property of the stored
  query: Cube resolves `"last 30 days"` at execution time, and `LoadResult.resolved_ranges` is
  the only thing that says which way it went.

**The agent authors and arranges; the page runs.** Refresh is a button and lives here rather than
in `agent/tools/dashboards.py`, because an agent that could trigger one turns "tidy up this
board" into two dozen warehouse queries. The rows themselves are `core.boards` — the agent writes
them, so they cannot live at this tier.
"""

from .routes import router

__all__ = ["router"]
