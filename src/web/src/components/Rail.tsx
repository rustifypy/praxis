/**
 * The mode switch, and the only thing on screen in every mode.
 *
 * Chat and data are two products with opposite shapes — one is an unbounded list of
 * ephemeral conversations, the other one heavily stateful object with destructive controls.
 * They shared a sidebar, which meant the chat list stayed live and clickable while you were
 * administering a database. Splitting the mode out here lets the column beside it belong
 * entirely to whichever one you are in.
 *
 * Dashboards sit between them: made in chat, read against the model, so the rail is in the
 * order the work happens.
 */

const CHAT = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20.5 11.6a8 8 0 0 1-8.6 8 9.1 9.1 0 0 1-2.8-.4L4.2 20.5l1.4-3.9a7.9 7.9 0 0 1-1.9-5 8 8 0 0 1 8.6-8 8.1 8.1 0 0 1 8.2 8Z" />
  </svg>
);

const DATA = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <ellipse cx="12" cy="6" rx="7.2" ry="2.9" />
    <path d="M4.8 6v11.8c0 1.6 3.2 2.9 7.2 2.9s7.2-1.3 7.2-2.9V6" />
    <path d="M4.8 11.9c0 1.6 3.2 2.9 7.2 2.9s7.2-1.3 7.2-2.9" />
  </svg>
);

const BOARD = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3.2" y="3.8" width="7.4" height="7.4" rx="1.5" />
    <rect x="13.4" y="3.8" width="7.4" height="4.4" rx="1.5" />
    <rect x="3.2" y="13.9" width="7.4" height="6.3" rx="1.5" />
    <rect x="13.4" y="10.9" width="7.4" height="9.3" rx="1.5" />
  </svg>
);

export function Rail({
  view,
  jobRunning,
  onSelect,
}: {
  view: "chat" | "dashboards" | "data";
  /** Dotted onto the data icon, so a three-minute describe is visible from a chat. */
  jobRunning: boolean;
  onSelect: (view: "chat" | "dashboards" | "data") => void;
}) {
  return (
    <nav className="rail">
      <span className="rail-brand" title="analyst">
        <span className="dot" />
      </span>

      <button
        className={`rail-tab ${view === "chat" ? "active" : ""}`}
        onClick={() => onSelect("chat")}
        title="Chat"
        aria-label="Chat"
        aria-current={view === "chat"}
      >
        {CHAT}
        <span className="rail-label">Chat</span>
      </button>

      <button
        className={`rail-tab ${view === "dashboards" ? "active" : ""}`}
        onClick={() => onSelect("dashboards")}
        title="Dashboards"
        aria-label="Dashboards"
        aria-current={view === "dashboards"}
      >
        {BOARD}
        <span className="rail-label">Boards</span>
      </button>

      <button
        className={`rail-tab ${view === "data" ? "active" : ""}`}
        onClick={() => onSelect("data")}
        title="Data"
        aria-label="Data"
        aria-current={view === "data"}
      >
        {DATA}
        {jobRunning && <span className="rail-badge" aria-label="a job is running" />}
        <span className="rail-label">Data</span>
      </button>
    </nav>
  );
}
