/**
 * The model's files in its only two groups, `cubes/` and `views/`. A solid dot is unsaved text; a
 * hollow one is saved to the draft but not yet published.
 */

import { Badge } from "./Badge";
import { Button } from "./Button";
import { Empty } from "./Empty";
export function FileTree({
  files,
  open,
  unsaved,
  dirty,
  onOpen,
  onNew,
  editable,
}: {
  files: string[];
  open: string;
  unsaved: Set<string>;
  /** Saved to the draft, not yet published. */
  dirty: Set<string>;
  onOpen: (path: string) => void;
  onNew: () => void;
  editable: boolean;
}) {
  const groups: { head: string; prefix: string; blurb: string }[] = [
    { head: "Cubes", prefix: "cubes/", blurb: "One per table. Append-only." },
    { head: "Views", prefix: "views/", blurb: "What a question can reach." },
  ];

  return (
    <div className="tree">
      {groups.map(({ head, prefix, blurb }) => {
        const inGroup = files.filter((path) => path.startsWith(prefix));
        return (
          <section className="tree-group" key={prefix}>
            <h4>
              {head} <Badge>{inGroup.length}</Badge>
            </h4>
            <p className="tree-blurb">{blurb}</p>
            <ul>
              {inGroup.map((path) => (
                <li key={path}>
                  <button
                    className={`tree-file ${open === path ? "on" : ""}`}
                    onClick={() => onOpen(path)}
                    title={path}
                  >
                    <span
                      className={`tree-mark ${unsaved.has(path) ? "dirty" : dirty.has(path) ? "staged" : ""}`}
                      aria-hidden
                    />
                    {path.slice(prefix.length)}
                  </button>
                </li>
              ))}
              {inGroup.length === 0 && <li><Empty tight>none</Empty></li>}
            </ul>
          </section>
        );
      })}

      {editable && (
        <div className="tree-new">
          <Button onClick={onNew}>+ View</Button>
        </div>
      )}
    </div>
  );
}
