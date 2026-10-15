import { useState } from "react";

import type { Impact, RefDiff } from "../sources";
import { Button } from "./Button";

/**
 * Landing a branch on main. The server refuses a merge that breaks saved queries; overriding is a
 * differently worded button, never a checkbox someone could miss.
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
  /** Null while loading. */
  diff: RefDiff | null;
  busy: boolean;
  onCancel: () => void;
  onMerge: (force: boolean, deleteAfter: boolean) => void;
}) {
  const [deleteAfter, setDeleteAfter] = useState(true);
  const breaks = diff?.impact?.broken.length ?? 0;
  const nothing = diff !== null && diff.changed.length === 0;

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
 * What a merge would break, with the nearest surviving names. A clean result is stated, since
 * silence reads as "not checked".
 */
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
