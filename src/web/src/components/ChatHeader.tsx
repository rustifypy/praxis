import type { SourceRow } from "../sources";
import { healthOf, servingNote } from "../sources";

/**
 * What this conversation is querying, in one line.
 *
 * Chat had no header at all: "what am I asking about?" was answered by the sidebar footer
 * rendering `CUBE_URL` as a bare `127.0.0.1:4001`, which says nothing about the database
 * behind it or whether it is built.
 *
 * The chip is a link out, not a disclosure. What can be asked about, and the grounding the
 * agent already believes, are the wrong depth for a dropdown over a live transcript — they
 * live on the data page, one deliberate click away.
 */
export function ChatHeader({
  source,
  cubeUrl,
  onShowData,
}: {
  /** The connection, or null before one has been made. */
  source: SourceRow | null;
  cubeUrl: string;
  onShowData: () => void;
}) {
  if (!source) {
    return (
      <header className="chat-head">
        <button className="src-chip warn" onClick={onShowData}>
          <span className="health-dot warn" />
          <span className="src-chip-name">No database connected</span>
          <span className="src-chip-meta">
            {cubeUrl ? cubeUrl.replace(/^https?:\/\//, "") : "CUBE_URL is unset"} — connect one
          </span>
        </button>
      </header>
    );
  }

  const health = healthOf(source);

  return (
    <header className="chat-head">
      <button className="src-chip" onClick={onShowData} title="Manage the data source">
        <span className={`health-dot ${health.tone}`} />
        <span className="src-chip-name">{source.label}</span>
        {/* Only off main. The checked-out branch is the model being queried, and an answer
            from an unmerged model must not look like an answer from main. */}
        {source.branch && source.branch !== "main" && (
          <span className="src-chip-branch">{source.branch}</span>
        )}
        {/* The size of the model, in the one line there is room for. It is what tells you
            the model is actually built rather than merely connected, without opening
            anything. */}
        <span className="src-chip-meta">{servingNote(source)}</span>
      </button>
    </header>
  );
}
