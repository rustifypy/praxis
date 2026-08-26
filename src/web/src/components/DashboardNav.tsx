import { ago } from "../hooks";
import type { DashboardRow } from "../types";
import { Button } from "./Button";
import { Empty } from "./Empty";

/** The dashboard list column. */
export function DashboardNav({
  dashboards,
  selected,
  onOpen,
  onCreate,
}: {
  dashboards: DashboardRow[];
  selected: string | null;
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <nav className="nav">
      <div className="nav-head">
        <h2>Dashboards</h2>
        <Button className="nav-add" onClick={onCreate} title="New dashboard" aria-label="New dashboard">
          +
        </Button>
      </div>

      {dashboards.length === 0 ? (
        <Empty tight>None yet. Create one, then ask in chat to add tiles to it.</Empty>
      ) : (
        <ul className="nav-list">
          {dashboards.map((board) => (
            <li key={board.id}>
              <button
                className={`nav-row ${board.id === selected ? "active" : ""}`}
                onClick={() => onOpen(board.id)}
              >
                <span className="nav-row-name">{board.title}</span>
                <span className="nav-row-sub">
                  {board.tiles} tile{board.tiles === 1 ? "" : "s"} ·{" "}
                  {board.refreshed_at ? ago(board.refreshed_at) : "never refreshed"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
