import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../sources";
import type { JobState, StageName, TableCache } from "../sources";
import { Button } from "./Button";
import { JobProgress } from "./JobProgress";
import { TablePicker } from "./TablePicker";

/**
 * Building the data model, for onboarding and the data page's Rebuild. Stages are queued here
 * and started one per finished job, since the server refuses a stage before its predecessor.
 */
export function BuildModel({
  job,
  rebuilding,
  onJob,
  onRunning,
}: {
  job: JobState | null;
  rebuilding: boolean;
  onJob: (job: JobState) => void;
  /**
   * Whether a stage is running or queued. `onboarded` goes true once cubes land, mid-sequence,
   * so the wizard needs this to avoid unmounting the queue.
   */
  onRunning?: (running: boolean) => void;
}) {
  const [catalog, setCatalog] = useState<TableCache | null>(null);
  const [enrich, setEnrich] = useState(true);
  const [queue, setQueue] = useState<StageName[]>([]);
  const [error, setError] = useState("");
  const [failed, setFailed] = useState<{ stage: string; message: string } | null>(null);
  // The job whose ending was already handled, so the effect fires once per job.
  const settled = useRef("");
  const asked = useRef(false);

  const running = Boolean(job && !job.done);

  useEffect(() => {
    onRunning?.(running || queue.length > 0);
  }, [running, queue.length, onRunning]);

  const run = useCallback(
    async (stage: StageName, body: { schema_name?: string; tables?: string[] | null } = {}) => {
      setError("");
      try {
        onJob(await api.runStage(stage, body));
      } catch (err) {
        setError(String(err instanceof Error ? err.message : err));
        setQueue([]);
      }
    },
    [onJob],
  );

  // Read on arrival. `asked`, not `catalog`: an empty database would be re-read forever.
  useEffect(() => {
    if (asked.current || running) return;
    asked.current = true;
    void (async () => {
      try {
        const current = await api.tables();
        setCatalog(current);
        if (!current.read) await run("read");
      } catch (err) {
        setError(String(err instanceof Error ? err.message : err));
      }
    })();
  }, [running, run]);

  useEffect(() => {
    if (!job?.done || settled.current === job.id) return;
    settled.current = job.id ?? "";
    const stage = job.steps?.[0] ?? "";

    void api.tables().then(setCatalog).catch(() => {});

    if (job.error) {
      setQueue([]);
      setFailed({ stage, message: job.error });
      return;
    }
    setFailed(null);
    if (queue.length) {
      const [next, ...rest] = queue;
      setQueue(rest);
      void run(next);
    }
  }, [job?.id, job?.done, job?.error, job?.steps, queue, run]);

  const build = (schema: string, tables: string[] | null) => {
    setQueue(enrich ? ["describe", "views"] : []);
    void run("cubes", { schema_name: schema, tables });
  };

  return (
    <div className="build">
      {error && <p className="banner bad">{error}</p>}

      {failed && (
        <p className={`banner ${failed.stage === "cubes" || failed.stage === "read" ? "bad" : "warn"}`}>
          {failed.stage === "cubes" || failed.stage === "read" ? (
            <>
              {failed.message} — nothing was replaced, so try again once that is addressed.
            </>
          ) : (
            <>
              The cubes are built and Cube is serving them. The {failed.stage} step failed:{" "}
              {failed.message}. You can carry on and ask the model editor's assistant for
              descriptions and views instead.
            </>
          )}
        </p>
      )}

      {job && <JobProgress job={job} onCancel={() => void api.cancelJob()} />}

      {catalog?.notes.map((note) => (
        <p key={note} className="banner">
          {note}
        </p>
      ))}
      {catalog?.truncated && (
        <p className="banner warn">
          The catalog read hit a row limit, so some keys or statistics are missing — check the
          joins before trusting a wide schema.
        </p>
      )}

      {!catalog?.read ? (
        // The picker's own Re-read is unreachable when the first read failed.
        <div className="stage-cta">
          <p className="stage-hint">
            {running ? "Reading the database…" : "The database has not been read yet."}
          </p>
          {!running && (
            <Button variant="primary" onClick={() => void run("read")}>
              Read the database
            </Button>
          )}
        </div>
      ) : (
        <>
          <TablePicker
            catalog={catalog}
            busy={running}
            label="Build the model from {n} tables"
            confirm={
              rebuilding
                ? "Rebuilding replaces every file in main in one commit. What is there now stays " +
                  "in the store's history, but any hand edits to main are overwritten. Continue?"
                : ""
            }
            extra={
              <label className="build-enrich">
                <input
                  type="checkbox"
                  checked={enrich}
                  onChange={(e) => setEnrich(e.target.checked)}
                  disabled={running}
                />
                <span>
                  Also write descriptions and views with AI
                  <small>
                    One model call per table, so this is the slow part. Skip it and ask the model
                    editor's assistant for them later.
                  </small>
                </span>
              </label>
            }
            onRun={build}
            onReread={() => void run("read")}
          />
          {!enrich && (
            <p className="stage-hint">
              Without this the model is cubes only: no descriptions, no views, and the agent
              answering questions has less to go on.
            </p>
          )}
        </>
      )}
    </div>
  );
}
