import { useState } from "react";

import type { FlowSummary } from "../types";

import { Button } from "./Button";
import { Empty } from "./Empty";

/**
 * The flows list. Whether a flow runs is the server's `ok`, never derived here. Creating asks only
 * for a name: the folder name is the one thing editing a file can't change later.
 */
export function FlowNav({
  flows,
  selected,
  onOpen,
  onCreate,
}: {
  flows: FlowSummary[];
  selected: string | null;
  onOpen: (name: string) => void;
  onCreate: (name: string, title: string) => Promise<void>;
}) {
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [failed, setFailed] = useState("");

  const create = async () => {
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (!slug) return;
    setFailed("");
    try {
      await onCreate(slug, name.trim());
      setNaming(false);
      setName("");
    } catch (error) {
      setFailed(error instanceof Error ? error.message : "could not create that");
    }
  };

  return (
    <nav className="nav">
      <div className="nav-head">
        <h2>Flows</h2>
        <Button
          className="nav-add"
          onClick={() => setNaming((was) => !was)}
          title="New flow"
          aria-label="New flow"
        >
          +
        </Button>
      </div>

      {naming && (
        <form
          className="flow-new"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <input
            autoFocus
            value={name}
            placeholder="weekly revenue"
            onChange={(e) => setName(e.target.value)}
          />
          <Button type="submit" variant="primary" disabled={!name.trim()}>
            Create
          </Button>
          {failed && <div className="editor-note bad">{failed}</div>}
        </form>
      )}

      <ul className="nav-list">
        {flows.map((flow) => (
          <li key={flow.name}>
            <button
              className={`nav-row ${selected === flow.name ? "active" : ""}`}
              onClick={() => onOpen(flow.name)}
              title={flow.description || flow.title}
            >
              <span className="nav-row-name">{flow.title || flow.name}</span>
              <span className="nav-row-count">{flow.phases.length}</span>
              {/* A note, not an alarm: a flow being built is normally not runnable yet. */}
              <span className="nav-row-sub" title={flow.errors.join("\n")}>
                {flow.phases.length === 1 ? "1 phase" : `${flow.phases.length} phases`}
                {flow.ok ? "" : " · needs work"}
              </span>
            </button>
          </li>
        ))}
        {flows.length === 0 && (
          <li>
            <Empty tight>
              No flows yet. A flow is a folder of numbered steps you run again and again.
            </Empty>
          </li>
        )}
      </ul>
    </nav>
  );
}
