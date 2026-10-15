/**
 * A proposed model file, as a card that opens the review in the editor pane (which alone applies
 * hunks). Counts are computed here because only the browser knows the editor's current text.
 */

import { Button } from "./Button";
export function Proposal({
  path,
  text,
  current,
  reviewed,
  onReview,
}: {
  path: string;
  /** Null for a proposed deletion. */
  text: string | null;
  current: string;
  reviewed: boolean;
  onReview: () => void;
}) {
  const removing = text === null;
  const { added, removed } = removing
    ? { added: 0, removed: current ? current.split("\n").length : 0 }
    : countLines(current, text);

  return (
    <div className={`proposal ${reviewed ? "done" : ""}`}>
      <span className="proposal-what">
        {removing ? "Delete" : "Change"} <code>{path}</code>
      </span>
      {!removing && (
        <span className="proposal-count">
          <span className="added">+{added}</span> <span className="removed">−{removed}</span>
        </span>
      )}
      <span className="editor-spacer" />
      <Button variant={reviewed ? "default" : "primary"} onClick={onReview}>
        {reviewed ? "Open again" : "Review"}
      </Button>
    </div>
  );
}

/** Lines added and removed, by line multiset rather than a real diff; the size is what matters. */
function countLines(before: string, after: string): { added: number; removed: number } {
  const counts = new Map<string, number>();
  for (const line of before.split("\n")) counts.set(line, (counts.get(line) ?? 0) + 1);
  let added = 0;
  for (const line of after.split("\n")) {
    const seen = counts.get(line) ?? 0;
    if (seen > 0) counts.set(line, seen - 1);
    else added += 1;
  }
  let removed = 0;
  for (const left of counts.values()) removed += left;
  return { added, removed };
}
