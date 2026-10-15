import { Badge } from "./Badge";
import { Button } from "./Button";

/**
 * What has changed on this branch. Unsaved (solid) text is browser-only, so Revert is a local
 * undo; draft (hollow) changes live on the server, so Discard asks the store to restore the file.
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
  unsaved: Set<string>;
  dirty: Set<string>;
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
            {/* One undo only: Discard under unsaved typing would skip past it. */}
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
