import { useEffect, useMemo, useRef } from "react";

import { duration, useElapsed } from "../hooks";
import type { JobEvent, JobState } from "../sources";
import { ACTION_LABEL } from "../sources";

/** Per-step state read off the event stream, so nothing has to be tracked separately. */
function statusOf(job: JobState, step: string): "waiting" | "running" | "done" | "failed" {
  let state: "waiting" | "running" | "done" | "failed" = "waiting";
  for (const event of job.events ?? []) {
    if (event.data.step !== step) continue;
    if (event.type === "step_start") state = "running";
    if (event.type === "step_end") state = event.data.ok ? "done" : "failed";
  }
  return state;
}

/** The last progress line for the running step: "described 7 of 12 — invoice_line". */
function progressOf(job: JobState): string {
  const events = job.events ?? [];
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (event.type !== "progress") continue;
    const { done, total, table, message } = event.data;
    if (typeof total === "number" && typeof done === "number") {
      return `${done} of ${total}${table ? ` — ${table}` : ""}`;
    }
    if (message) return String(message);
  }
  return "";
}

function logsOf(events: JobEvent[]): string {
  return events
    .filter((event) => event.type === "log" && event.data.message)
    .map((event) => String(event.data.message))
    .join("\n");
}

/**
 * The seconds counter, alone.
 *
 * Its own component because `useElapsed` ticks ten times a second, and re-rendering the
 * whole panel that often would rescan every event of a four-hundred-table describe on
 * each tick.
 */
function Elapsed({ since, running }: { since?: number; running: boolean }) {
  const elapsed = useElapsed(since, running);
  return <span className="job-time">{duration(elapsed)}</span>;
}

/**
 * The running job, inline in the source it belongs to.
 *
 * Reconnecting is not special-cased: the server replays its buffer before going live, so
 * a refresh mid-describe draws exactly what was on screen before.
 */
export function JobProgress({ job, onCancel }: { job: JobState; onCancel: () => void }) {
  const events = job.events ?? [];
  // Derived from the event list, so they recompute when it grows and not on every tick.
  const progress = useMemo(() => progressOf(job), [events]);
  const logs = useMemo(() => logsOf(events), [events]);
  const states = useMemo(
    () => Object.fromEntries((job.steps ?? []).map((step) => [step, statusOf(job, step)])),
    [events, job.steps],
  );
  const tail = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (tail.current) tail.current.scrollTop = tail.current.scrollHeight;
  }, [logs]);

  return (
    <div className={`job ${job.done ? "done" : "live"}`}>
      <div className="job-head">
        <span className="job-title">
          {job.done ? (job.error ? "Failed" : "Finished") : `Working on ${job.source}`}
        </span>
        <Elapsed since={job.started_at ? job.started_at * 1000 : undefined} running={!job.done} />
        {!job.done && (
          <button className="danger" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      <ol className="job-steps">
        {(job.steps ?? []).map((step) => {
          const state = states[step] ?? "waiting";
          return (
            <li key={step} className={state}>
              <span className="tick" aria-hidden>
                {state === "done" ? "✓" : state === "failed" ? "✕" : state === "running" ? "•" : "·"}
              </span>
              <span className="job-step-name">{ACTION_LABEL[step] ?? step}</span>
              {state === "running" && progress && <span className="job-step-note">{progress}</span>}
            </li>
          );
        })}
      </ol>

      {job.error && <p className="job-error">{job.error}</p>}

      {logs && (
        <pre className="job-logs" ref={tail}>
          {logs}
        </pre>
      )}
    </div>
  );
}
