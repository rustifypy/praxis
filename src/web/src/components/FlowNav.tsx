import { useState } from "react";

import type { FlowSummary } from "../types";

import { Button } from "./Button";
import { Empty } from "./Empty";

/**
 * The flows list: the column beside the page, like the chat list and the dashboard list.
 *
 * Each row says how many phases a flow has and whether it currently runs. That second marker is
 * the useful one and it is *not* derived here — the server sends `ok`, because "is this a flow
 * that could run" is `contract.load`'s answer and a second implementation in the browser would
 * eventually disagree with the one the run uses.
 *
 * Creating a flow asks for a name and nothing else. A folder name is the flow's identity, so it
 * is the one field that cannot be changed later by editing a file, which is why it is the one
 * field asked for here — the title and the phases are the authoring conversation's job.
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
      // The title starts as the words that were typed, since that is what they meant; the flow
      // agent renames it once the conversation knows what the flow is actually for.
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
            {/* `.nav-*` is the shared sidebar vocabulary, reused rather than paralleled — the
                stylesheet says outright that a second set of near-identical row rules is how the
                two start to drift. */}
            <button
              className={`nav-row ${selected === flow.name ? "active" : ""}`}
              onClick={() => onOpen(flow.name)}
              title={flow.description || flow.title}
            >
              <span className="nav-row-name">{flow.title || flow.name}</span>
              <span className="nav-row-count">{flow.phases.length}</span>
              {/* A flow being built is not runnable yet, which is normal — so this is a note and
                  not an alarm. It earns its place because the alternative is pressing Run and
                  reading the refusal. */}
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
