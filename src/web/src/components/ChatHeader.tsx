import { useState } from "react";

import { useDismiss } from "../hooks";
import type { SourceRow } from "../sources";
import { healthOf, servingNote } from "../sources";

/**
 * Which source this conversation queries, and how to change it.
 *
 * Chat had no header at all: "what am I querying?" was answered by the sidebar footer
 * rendering `CUBE_URL` as a bare `127.0.0.1:4001`, a port number nobody can map back to a
 * source without opening the sources panel and comparing.
 *
 * The panel is a source *switcher* and nothing more. It briefly carried the served model
 * and this source's grounding, which was the wrong depth for a dropdown over a live
 * transcript — clicking the chip to check which database you were on opened an admin
 * screen. Both live on the source page now, via "Manage sources".
 */
export function ChatHeader({
  sources,
  cubeUrl,
  onShowSources,
}: {
  sources: SourceRow[];
  cubeUrl: string;
  onShowSources: (name?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));

  const active = sources.find((source) => source.active) ?? null;
  const health = active ? healthOf(active) : null;

  if (!active) {
    return (
      <header className="chat-head">
        <button className="src-chip warn" onClick={() => onShowSources()}>
          <span className="health-dot warn" />
          <span className="src-chip-name">No active source</span>
          <span className="src-chip-meta">
            {cubeUrl ? `${cubeUrl.replace(/^https?:\/\//, "")} matches no source` : "CUBE_URL is unset"}
          </span>
        </button>
      </header>
    );
  }

  return (
    <header className="chat-head">
      <div className="src-chip-wrap" ref={ref}>
        <button className="src-chip" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className={`health-dot ${health?.tone}`} />
          <span className="src-chip-name">{active.name}</span>
          {/* Only off main. The checked-out branch is the model being queried, and an
              answer from an unmerged model must not look like an answer from main. */}
          {active.branch && active.branch !== "main" && (
            <span className="src-chip-branch">{active.branch}</span>
          )}
          {/* The size of the model, in the one line there is room for. It is what tells
              you the source is actually built rather than merely connected, without
              opening anything. */}
          <span className="src-chip-meta">{servingNote(active)}</span>
          <span className="src-chip-caret" aria-hidden>
            ▾
          </span>
        </button>

        {open && (
          <div className="src-panel">
            <div className="src-panel-switch">
              <h4>Querying</h4>
              {sources.map((source) => (
                <div
                  key={source.name}
                  className={`src-switch-row ${source.active ? "on" : ""}`}
                  title={
                    source.active
                      ? undefined
                      : `Set CUBE_URL=${source.url} and restart analyst to query this one`
                  }
                >
                  <span className={`health-dot ${healthOf(source).tone}`} />
                  <span className="src-switch-name">{source.name}</span>
                  <span className="src-switch-meta">{servingNote(source)}</span>
                  {source.active ? (
                    <span className="badge accent">active</span>
                  ) : (
                    // The switch itself is not built yet — swapping mid-conversation needs
                    // a decision about turns already in flight against the old source.
                    // Until then this is the instruction, in text rather than in a tooltip
                    // on a disabled button, which several browsers never show at all.
                    <span className="src-switch-how">
                      <code>CUBE_URL={source.url}</code> + restart
                    </span>
                  )}
                </div>
              ))}
              {/* Where the model and this source's grounding are — deliberately a link
                  out rather than another disclosure inside a dropdown. */}
              <button className="link" onClick={() => onShowSources(active.name)}>
                Manage sources →
              </button>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
