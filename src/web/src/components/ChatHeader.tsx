import type { SourceRow } from "../sources";
import { healthOf, servingNote } from "../sources";

/** What this conversation is querying, in one line; the chip links to the data page. */
export function ChatHeader({
  source,
  cubeUrl,
  onShowData,
}: {
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
            {cubeUrl
              ? `${cubeUrl.replace(/^https?:\/\//, "")} — connect one`
              : "Connect one to ask about it"}
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
        <span className="src-chip-meta">{servingNote(source)}</span>
      </button>
    </header>
  );
}
