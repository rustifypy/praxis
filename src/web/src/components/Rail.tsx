import type { ReactNode } from "react";

import { type Theme, useTheme } from "../theme";

/**
 * The mode switch, the only thing on screen in every mode. Agents is the MCP page (handing these
 * tools out), not Flows.
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

export type Mode = "chat" | "dashboards" | "data" | "flows" | "agents";

const FLOWS = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3.4" y="3.6" width="17.2" height="4.6" rx="1.5" />
    <rect x="3.4" y="12.2" width="17.2" height="4.6" rx="1.5" />
    <path d="M7.5 8.2v4M7.5 16.8v3.6" />
  </svg>
);

const AGENTS = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 2.8v5.4M15 2.8v5.4" />
    <path d="M6.4 8.2h11.2v3a5.6 5.6 0 0 1-5.6 5.6 5.6 5.6 0 0 1-5.6-5.6Z" />
    <path d="M12 16.8v4.4" />
  </svg>
);

/** Each face shows the theme in force now, not what a press would change to. */
const THEMES: Record<Theme, { icon: ReactNode; label: string; next: Theme }> = {
  auto: {
    label: "Auto",
    next: "light",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="8.2" />
        <path d="M12 3.8v16.4a8.2 8.2 0 0 0 0-16.4Z" fill="currentColor" />
      </svg>
    ),
  },
  light: {
    label: "Light",
    next: "dark",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" />
      </svg>
    ),
  },
  dark: {
    label: "Dark",
    next: "auto",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20.2 14.4A8.4 8.4 0 0 1 9.6 3.8a8.4 8.4 0 1 0 10.6 10.6Z" />
      </svg>
    ),
  },
};

export function Rail({
  view,
  jobRunning,
  onSelect,
}: {
  view: Mode;
  jobRunning: boolean;
  onSelect: (view: Mode) => void;
}) {
  const [theme, setTheme] = useTheme();
  const face = THEMES[theme];
  return (
    <nav className="rail">
      <span className="rail-brand" title="Praxis">
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

      <button
        className={`rail-tab ${view === "flows" ? "active" : ""}`}
        onClick={() => onSelect("flows")}
        title="Flows"
        aria-label="Flows"
        aria-current={view === "flows"}
      >
        {FLOWS}
        <span className="rail-label">Flows</span>
      </button>

      <button
        className={`rail-tab ${view === "agents" ? "active" : ""}`}
        onClick={() => onSelect("agents")}
        title="Agents"
        aria-label="Agents"
        aria-current={view === "agents"}
      >
        {AGENTS}
        <span className="rail-label">Agents</span>
      </button>

      <button
        className="rail-tab rail-theme"
        onClick={() => setTheme(face.next)}
        title={`Theme: ${face.label.toLowerCase()}${theme === "auto" ? ", following the system" : ""}. Switch to ${THEMES[face.next].label.toLowerCase()}.`}
        aria-label={`Theme: ${face.label}`}
      >
        {face.icon}
        <span className="rail-label">{face.label}</span>
      </button>
    </nav>
  );
}
