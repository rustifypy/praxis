import * as api from "../sources";
import type { JobState, SourceRow, System } from "../sources";
import { STEP_LABEL, healthOf, nextStep, servingSummary } from "../sources";
import { Info } from "./Info";
import { JobProgress } from "./JobProgress";

/**
 * The data mode's landing page: every source, and everything wrong machine-wide.
 *
 * The rows are deliberately thin — the sidebar already lists the same sources, so this
 * page earns its place by carrying the system banners (docker, dev mode, Cube Store,
 * orphaned containers) that belong to no single source.
 */
export function Sources({
  system,
  sources,
  job,
  nextPort,
  onRefresh,
  onAdd,
  onOpen,
}: {
  system: System;
  sources: SourceRow[];
  job: JobState | null;
  nextPort: number;
  onRefresh: () => void;
  onAdd: () => void;
  onOpen: (name: string) => void;
}) {
  return (
    <div className="sources">
      <header className="sources-head">
        <h1>
          Data sources
          <Info label="About sources">
            One Cube container per source, started from its row in the sources database —
            credentials included, so nothing on disk can drift from it. The agent queries
            the one marked active, chosen by <code>CUBE_URL</code>.
          </Info>
        </h1>
        <button className="primary" onClick={onAdd} disabled={!system.ok}>
          + Add source
        </button>
      </header>

      {!system.ok && <p className="banner bad">{system.reason}</p>}
      {system.ok && system.dev_mode && (
        <p className="banner warn">
          Cube dev mode is on, so every source serves its playground and skips JWT
          verification. Fine locally; set <code>CUBEJS_DEV_MODE=false</code> and re-run
          Connect for anything else. Ports are bound to localhost either way.
        </p>
      )}
      {system.ok && !system.cubestore && (
        <p className="banner warn">
          Cube Store is not running — queries still work, pre-aggregations will not. Start
          it with <code>make up</code>.
        </p>
      )}
      {system.orphans?.length ? (
        <p className="banner warn">
          Containers with no source: {system.orphans.join(", ")}.{" "}
          <button
            className="link"
            onClick={() =>
              void Promise.all((system.orphans ?? []).map(api.removeOrphan)).then(onRefresh)
            }
          >
            Remove them
          </button>
        </p>
      ) : null}

      {sources.length === 0 && system.ok && (
        <div className="sources-empty">
          <h2>No sources yet</h2>
          <p>
            Add one and we will start its container, read its tables, and build a semantic
            model you can ask questions about.
          </p>
          <button className="primary" onClick={onAdd}>
            + Add source
          </button>
        </div>
      )}

      <div className="src-list">
        {sources.map((source) => {
          const health = healthOf(source);
          const upcoming = nextStep(source);
          return (
            <button
              key={source.name}
              className={`src-row ${source.active ? "active" : ""}`}
              onClick={() => onOpen(source.name)}
            >
              <span className={`health-dot ${health.tone}`} title={source.health.status} />
              <span className="src-row-name">{source.name}</span>
              <span className="src-row-what">
                {source.driver} · :{source.port}
              </span>
              <span className="src-row-model">
                {source.serving
                  ? servingSummary(source.serving)
                  : source.running
                    ? "no model yet"
                    : health.label}
              </span>
              {source.active && <span className="badge accent">active</span>}
              <span className="src-row-next">
                {upcoming ? `next: ${STEP_LABEL[upcoming]}` : "complete"}
              </span>
              <span className="src-row-go" aria-hidden>
                →
              </span>
            </button>
          );
        })}
      </div>

      {/* A job outlives the page it was started from. */}
      {job && <JobProgress job={job} onCancel={() => void api.cancelJob()} />}

      {sources.length > 0 && (
        <p className="sources-foot">
          Next free port: {nextPort}. Cube Store is shared by every source.
        </p>
      )}
    </div>
  );
}
