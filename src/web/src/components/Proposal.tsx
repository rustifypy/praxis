/**
 * A model file the editor agent wants to write, as a card in the transcript.
 *
 * **The card is a pointer, not the review.** Clicking it opens the file in the editor pane, where
 * the diff is shown with an accept and a reject button per hunk. Rendering the hunks here too
 * would mean a second implementation of "apply a hunk", and the second one is always what
 * produces YAML neither side wrote.
 *
 * The counts are computed here rather than shipped, because they are a property of the diff
 * against whatever the editor currently holds — and the server does not know what that is.
 * Staging lives in the browser.
 *
 * A null `text` is a proposed deletion, which is a different sentence rather than a diff of one
 * file against nothing.
 */
export function Proposal({
  path,
  text,
  current,
  reviewed,
  onReview,
}: {
  path: string;
  /** The proposed body, or null for a proposed deletion. */
  text: string | null;
  /** What the editor holds for this file right now, so the counts describe the real diff. */
  current: string;
  /** Already opened for review, so the card says so instead of reopening a settled diff. */
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
      <button className={reviewed ? "" : "primary"} onClick={onReview}>
        {reviewed ? "Open again" : "Review"}
      </button>
    </div>
  );
}

/**
 * How many lines the proposal adds and removes.
 *
 * A line-multiset comparison and not a real diff: the editor pane runs the actual diff, and
 * duplicating that algorithm to put a number on a card would be two answers to one question.
 * What this needs to be is right about the *size* of a change, and it is — a moved line counts
 * as neither, which is the same thing a proper diff would say.
 */
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
