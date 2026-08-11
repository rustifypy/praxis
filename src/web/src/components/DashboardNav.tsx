import { ago } from "../hooks";
import type { DashboardRow } from "../types";

/**
 * The dashboard list column.
 *
 * The dot is whether this board's source is the one being queried — a board on another source
 * is readable and not refreshable, which is worth knowing before clicking in.
 */
export function DashboardNav({
  dashboards,
  selected,
  activeSource,
  onOpen,
  onCreate,
}: {
  dashboards: DashboardRow[];
  selected: string | null;
  /** The source the portal is querying. A row on any other source cannot be refreshed. */
  activeSource: string;
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <nav className="nav">
      <div className="nav-head">
        <h2>Dashboards</h2>
        <button className="nav-add" onClick={onCreate} title="New dashboard" aria-label="New dashboard">
          +
        </button>
      </div>

      {dashboards.length === 0 ? (
        <p className="empty">
          None yet. Create one, then ask in chat to add tiles to it.
        </p>
      ) : (
        <ul className="nav-list">
          {dashboards.map((board) => (
            <li key={board.id}>
              <button
                className={`nav-row ${board.id === selected ? "active" : ""}`}
                onClick={() => onOpen(board.id)}
              >
                <span
                  className={`health-dot ${board.source_name === activeSource ? "ok" : "warn"}`}
                  title={
                    board.source_name === activeSource
                      ? `on ${board.source_name} — refreshable`
                      : `built on ${board.source_name}, which is not the source being queried`
                  }
                />
                <span className="nav-row-name">{board.title}</span>
                <span className="nav-row-sub">
                  {board.tiles} tile{board.tiles === 1 ? "" : "s"} ·{" "}
                  {board.refreshed_at ? ago(board.refreshed_at) : "never refreshed"}
                </span>
                {board.source_name !== activeSource && (
                  <span className="nav-row-tag">on {board.source_name}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
