/**
 * The model's files, grouped by the directory that gives them their meaning.
 *
 * Two groups and not an arbitrary tree, because the model has exactly two kinds of file and the
 * directory *is* the kind: `cubes/` is what exists and `views/` is what a question can reach. A
 * generic recursive tree would render the same two folders while implying there could be others.
 *
 * The grouping is derived from the path rather than shipped by the server. The route already
 * filters to what may be opened, and the prefix is the kind — a `kind` field beside it would be
 * a second way to say the same thing, free to disagree.
 *
 * Two markers, both dots on the file rather than only in the bar, because "what have I changed"
 * is a question about the tree and the bar can only count. A solid dot is text typed and not yet
 * saved to the draft; a hollow one is saved to the draft and not yet published.
 */
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
  /** The path showing in the editor, or "". */
  open: string;
  /** Paths typed into and not yet saved to the draft. */
  unsaved: Set<string>;
  /** Paths the draft has changed against the branch — saved here, not yet published. */
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
              {head} <span className="tree-count">{inGroup.length}</span>
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
                    {/* The prefix is in the group header, so repeating it on every row wastes the
                        width the name needs. */}
                    {path.slice(prefix.length)}
                  </button>
                </li>
              ))}
              {inGroup.length === 0 && <li className="tree-empty">none</li>}
            </ul>
          </section>
        );
      })}

      {/* Creating a file is here rather than in the bar: it is a thing you do *to the tree*, and
          the bar is about the branch as a whole. */}
      {editable && (
        <div className="tree-new">
          <button onClick={onNew}>+ View</button>
        </div>
      )}
    </div>
  );
}
