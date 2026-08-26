import { Badge } from "./Badge";
import { Button } from "./Button";

/**
 * What has changed on this branch, and the two ways to take it back.
 *
 * **The dots on the tree and the tabs say *that* something changed; this says what and offers to
 * undo it.** They were the only answer before, which meant "what have I actually touched" was a
 * question you answered by scanning two groups of files for a 6px mark — and the only count was
 * buried in the label of the Save button.
 *
 * Two states, and they are not the same question. A **solid** mark is text typed into the editor
 * and not written to the branch's draft: it lives in the browser and a reload loses it, so Revert
 * is a local undo. A **hollow** one is in the draft on the server and not yet published: it
 * survives a reload, and Discard is a request to the store to put the file back. Sharing one
 * vocabulary with the tree matters more than being explicit here — the same mark means the same
 * thing in all three places it appears.
 */
export function Changes({
  unsaved,
  dirty,
  open,
  busy,
  onOpen,
  onRevert,
  onDiscard,
}: {
  /** Typed and not written through to the draft. */
  unsaved: Set<string>;
  /** In the draft and not published. */
  dirty: Set<string>;
  /** The path showing in the editor, or "". */
  open: string;
  busy: boolean;
  onOpen: (path: string) => void;
  onRevert: (path: string) => void;
  onDiscard: (path: string) => void;
}) {
  const paths = [...new Set([...unsaved, ...dirty])].sort();
  if (!paths.length) return null;

  return (
    <section className="changes">
      <h4>
        Changes <Badge>{paths.length}</Badge>
      </h4>
      {unsaved.size > 0 && (
        <p className="changes-warn">
          {unsaved.size} not saved to the draft — a reload discards{" "}
          {unsaved.size === 1 ? "it" : "them"}.
        </p>
      )}
      <ul>
        {paths.map((path) => (
          <li key={path}>
            <button
              className={`tree-file ${open === path ? "on" : ""}`}
              onClick={() => onOpen(path)}
              title={path}
            >
              <span className={`tree-mark ${unsaved.has(path) ? "dirty" : "staged"}`} aria-hidden />
              {path.slice(path.indexOf("/") + 1)}
            </button>
            {/* Whichever undo applies to the state it is actually in. Both at once would offer
                to discard a draft change that the typing on top of it has not reached yet. */}
            {unsaved.has(path) ? (
              <Button variant="subtle" size="sm" className="undo" disabled={busy} onClick={() => onRevert(path)}>
                Revert
              </Button>
            ) : (
              <Button variant="subtle" size="sm" className="undo" disabled={busy} onClick={() => onDiscard(path)}>
                Discard
              </Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
