import { Badge } from "./Badge";
import { Empty } from "./Empty";

/** A flow's files, grouped by phase rather than as a generic tree: a phase is the unit. */

const ROOT = { head: "Flow", blurb: "What it is for, and its title." };
const CONFIG = { head: "Reference", blurb: "Rules every run shares." };

/** `phases/01_pull/phase.yml` → `01_pull`, and `""` for anything that is not in a phase. */
function phaseOf(path: string): string {
  const parts = path.split("/");
  return parts[0] === "phases" && parts.length > 2 ? parts[1] : "";
}

export function FlowFiles({
  files,
  open,
  unsaved,
  onOpen,
}: {
  files: string[];
  open: string;
  unsaved: Set<string>;
  onOpen: (path: string) => void;
}) {
  const root = files.filter((path) => !path.includes("/"));
  const config = files.filter((path) => path.startsWith("_config/"));
  // `files` arrives sorted, so Set order is execution order.
  const phases = [...new Set(files.map(phaseOf).filter(Boolean))];

  const row = (path: string, label: string) => (
    <li key={path}>
      <button
        className={`tree-file ${open === path ? "on" : ""}`}
        onClick={() => onOpen(path)}
        title={path}
      >
        <span className={`tree-mark ${unsaved.has(path) ? "dirty" : ""}`} aria-hidden />
        {label}
      </button>
    </li>
  );

  return (
    <div className="tree">
      <section className="tree-group">
        <h4>{ROOT.head}</h4>
        <p className="tree-blurb">{ROOT.blurb}</p>
        <ul>
          {root.map((path) => row(path, path))}
          {root.length === 0 && (
            <li>
              <Empty tight>none</Empty>
            </li>
          )}
        </ul>
      </section>

      {config.length > 0 && (
        <section className="tree-group">
          <h4>
            {CONFIG.head} <Badge>{config.length}</Badge>
          </h4>
          <p className="tree-blurb">{CONFIG.blurb}</p>
          <ul>{config.map((path) => row(path, path.slice("_config/".length)))}</ul>
        </section>
      )}

      {phases.map((phase) => {
        const inPhase = files.filter((path) => phaseOf(path) === phase);
        return (
          <section className="tree-group" key={phase}>
            <h4>{phase}</h4>
            <ul>
              {inPhase.map((path) => row(path, path.split("/").slice(2).join("/")))}
            </ul>
          </section>
        );
      })}

      {phases.length === 0 && (
        <section className="tree-group">
          <h4>Phases</h4>
          <p className="tree-blurb">
            None yet. Ask the flow agent what steps this work needs — it writes them.
          </p>
        </section>
      )}
    </div>
  );
}
