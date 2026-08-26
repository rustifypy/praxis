import { useState } from "react";

import type { Impact, RefDiff } from "../sources";
import { Button } from "./Button";

/**
 * Landing a branch on main: the patch, what it would break, and the two decisions.
 *
 * A dialog rather than a third editor mode. Merging is the one action here that changes what
 * every chat and every dashboard reads, and it is worth a screen that stops and asks — the old
 * version put it behind a Review button that swapped the editor pane, so the diff and the
 * consequence competed for the same space as the file you were editing.
 *
 * **Breakage refuses the merge by default**, server-side, and the override is a differently
 * worded button rather than a checkbox — a destructive action should not be reachable by missing
 * a tick. Deleting the branch afterwards *is* a checkbox, because it is reversible in the only
 * sense that matters: the commits are on main by then.
 */
export function MergeDialog({
  branch,
  main,
  diff,
  busy,
  onCancel,
  onMerge,
}: {
  branch: string;
  main: string;
  /** Null while it is still being fetched. */
  diff: RefDiff | null;
  busy: boolean;
  onCancel: () => void;
  onMerge: (force: boolean, deleteAfter: boolean) => void;
}) {
  const [deleteAfter, setDeleteAfter] = useState(true);
  const breaks = diff?.impact?.broken.length ?? 0;
  const nothing = diff !== null && diff.changed.length === 0;

  // `.modal-scrim` and `.modal` are the dialog chrome every other dialog here uses — the
  // placement, the scrim, the footer row. Only the width and the scrolling body are this
  // dialog's own, because a patch is taller than a confirmation.
  return (
    <div className="modal-scrim">
      <div className="modal merge-modal" role="dialog" aria-modal="true">
        <h2>
          Merge <code>{branch}</code> into <code>{main}</code>
        </h2>

        <div className="merge-body">
          {diff === null ? (
            <p className="muted">Reading the diff…</p>
          ) : nothing ? (
            <p className="banner">
              Nothing has changed on <code>{branch}</code>, so there is nothing to merge.
            </p>
          ) : (
            <>
              <ul className="merge-files">
                {diff.changed.map((file) => (
                  <li key={file.path}>
                    <span className={`merge-status s-${file.status}`}>{file.status}</span>
                    <code>{file.path}</code>
                  </li>
                ))}
              </ul>

              {diff.impact && <Breakage impact={diff.impact} />}

              <pre className="editor-diff">{diff.patch}</pre>

              <p className="stage-hint">
                {main} is what every chat and every dashboard reads, and Cube is serving the merged
                model within seconds. Cube compiling a measure does not make it correct, so read
                the diff.
              </p>
            </>
          )}
        </div>

        <footer>
          <label className="merge-drop">
            <input
              type="checkbox"
              checked={deleteAfter}
              onChange={(e) => setDeleteAfter(e.target.checked)}
            />
            Delete <code>{branch}</code> after merging
          </label>
          <span className="editor-spacer" />
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={breaks ? "danger" : "primary"}
            disabled={busy || nothing || diff === null}
            onClick={() => onMerge(breaks > 0, deleteAfter)}
          >
            {busy
              ? "Merging…"
              : breaks
                ? `Merge anyway, breaking ${breaks}`
                : `Merge into ${main}`}
          </Button>
        </footer>
      </div>
    </div>
  );
}

/**
 * What a merge would break, with the nearest surviving name so a fix is one edit.
 *
 * "No saved query breaks" is worth saying out loud rather than showing nothing: the absence of a
 * warning reads as "not checked", and this check is the whole reason the merge can be trusted.
 */
/** Not exported any more: the editor renders its own copy of this inside `Problems`, where it is
 *  one of four kinds of thing wrong with a draft rather than a panel of its own. */
function Breakage({ impact }: { impact: Impact }) {
  if (!impact.broken.length) {
    return (
      <p className="editor-note ok">No saved query breaks — {impact.checked} checked.</p>
    );
  }
  return (
    <div className="editor-note bad">
      <p>
        {impact.broken.length} of {impact.checked} saved queries would break:
      </p>
      <ul className="editor-broken">
        {impact.broken.map((b) => (
          <li key={`${b.kind}:${b.where}:${b.title}`}>
            Tile <strong>{b.title}</strong> ({b.where}) reads{" "}
            {b.members.map((m, i) => (
              <span key={m}>
                {i > 0 && ", "}
                <code>{m}</code>
                {b.nearest[m]?.length ? ` — closest: ${b.nearest[m].join(", ")}` : ""}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}
