import { useMemo, useState } from "react";

import type { JobState, SourceRow, StepName } from "../sources";
import { STEP_LABEL, healthOf, nextStep } from "../sources";

/**
 * The data mode's whole sidebar: sources, and the job running over one of them.
 *
 * These used to sit under the chat list as a link, a row of dots and a pill — three
 * representations of one concept, none of them the primary thing on screen. Here they are
 * the only thing on screen.
 */
export function DataNav({
  sources,
  selected,
  job,
  onOpen,
  onAll,
  onAdd,
  canAdd,
}: {
  sources: SourceRow[];
  /** The source whose page is showing, or null on the list. */
  selected: string | null;
  job: JobState | null;
  onOpen: (name: string) => void;
  onAll: () => void;
  onAdd: () => void;
  canAdd: boolean;
}) {
  const [filter, setFilter] = useState("");

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle ? sources.filter((s) => s.name.toLowerCase().includes(needle)) : sources;
  }, [sources, filter]);

  const running = Boolean(job && !job.done);

  return (
    <aside className="nav">
      <header className="nav-head">
        <h2>Sources</h2>
        <button
          className="nav-add"
          onClick={onAdd}
          disabled={!canAdd}
          title={canAdd ? "Add a source" : "Docker is not reachable"}
          aria-label="Add a source"
        >
          +
        </button>
      </header>

      {sources.length > 6 && (
        <input
          className="nav-filter"
          placeholder="Search sources"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}

      <div className="nav-list">
        <button
          className={`nav-row ${selected === null ? "active" : ""}`}
          onClick={onAll}
        >
          <span className="nav-row-name">All sources</span>
          <span className="nav-row-count">{sources.length}</span>
        </button>

        {shown.map((source) => {
          const health = healthOf(source);
          const upcoming = nextStep(source);
          return (
            <button
              key={source.name}
              className={`nav-row ${selected === source.name ? "active" : ""}`}
              onClick={() => onOpen(source.name)}
            >
              <span className={`health-dot ${health.tone}`} title={source.health.status} />
              <span className="nav-row-name">{source.name}</span>
              {source.active && <span className="nav-row-tag">active</span>}
              <span className="nav-row-sub">
                {upcoming ? `next: ${STEP_LABEL[upcoming]}` : "complete"}
              </span>
            </button>
          );
        })}

        {sources.length === 0 && <p className="empty">No sources yet.</p>}
        {sources.length > 0 && shown.length === 0 && (
          <p className="empty">Nothing matches “{filter}”.</p>
        )}
      </div>

      {/* A failed job stays until it is looked at. Gating this on `running` meant a
          failure vanished from every screen except the one source page it happened on. */}
      {job && (
        <button className={`job-pill ${running ? "live" : job.error ? "failed" : "done"}`} onClick={() => onOpen(job.source ?? "")}>
          <span className="job-pill-dot" />
          <span className="job-pill-text">
            {running
              ? job.current
                ? `${STEP_LABEL[job.current as StepName] ?? "Reading"} · ${job.source}`
                : `Working on ${job.source}`
              : job.error
                ? `${job.source} failed`
                : `${job.source} finished`}
          </span>
        </button>
      )}
    </aside>
  );
}
