/**
 * The mode switch, and the only thing on screen in both modes.
 *
 * Chat and sources are two products with opposite shapes — one is an unbounded list of
 * ephemeral conversations, the other a handful of heavily stateful objects with
 * destructive controls. They shared a sidebar, which meant the chat list stayed live and
 * clickable while you were administering a database. Splitting the mode out here lets the
 * column beside it belong entirely to whichever one you are in.
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

export function Rail({
  view,
  jobRunning,
  onSelect,
}: {
  view: "chat" | "sources";
  /** Dotted onto the data icon, so a three-minute describe is visible from a chat. */
  jobRunning: boolean;
  onSelect: (view: "chat" | "sources") => void;
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
        className={`rail-tab ${view === "sources" ? "active" : ""}`}
        onClick={() => onSelect("sources")}
        title="Data"
        aria-label="Data"
        aria-current={view === "sources"}
      >
        {DATA}
        {jobRunning && <span className="rail-badge" aria-label="a job is running" />}
        <span className="rail-label">Data</span>
      </button>
    </nav>
  );
}
