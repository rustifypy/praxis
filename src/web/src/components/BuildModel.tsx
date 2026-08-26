import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../sources";
import type { JobState, StageName, TableCache } from "../sources";
import { Button } from "./Button";
import { JobProgress } from "./JobProgress";
import { TablePicker } from "./TablePicker";

/**
 * Building the data model: read the database, choose what to model, run the pipeline.
 *
 * **One component, two callers** — the last step of onboarding and the Rebuild action on the data
 * page — which is what earns it a file. They are the same screen because they are the same work;
 * the only difference is that a rebuild replaces a model that already exists, which is a sentence
 * in a confirmation rather than a different flow.
 *
 * **`read` runs on arrival, unasked.** It caches a catalog and writes no model file, so there is
 * nothing to confirm — and without it the picker has nothing to show.
 *
 * **The stages are chained through the job, not through one request.** Each `POST /pipeline/{stage}`
 * answers as soon as the job exists, and `steps.action` refuses a stage whose predecessor has not
 * succeeded — so they cannot be fired together. The queue below starts the next one when the last
 * one lands, which also means a cancel stops the rest.
 *
 * **Cubes are required and enrichment is not.** Without cubes there is no model and nothing can
 * answer a question, so onboarding cannot end here. Descriptions and views are one model call per
 * table, they are skippable, and the editor's assistant can write them later — which is why a
 * failure in those two reports itself and lets you through rather than blocking.
 */
export function BuildModel({
  job,
  rebuilding,
  onJob,
  onRunning,
}: {
  /** The running job. The page owns the event stream; this only draws it and reacts to its end. */
  job: JobState | null;
  /** True on the data page, where a model already exists and would be replaced. */
  rebuilding: boolean;
  onJob: (job: JobState) => void;
  /**
   * Whether a sequence is in flight — a stage running, or one queued behind it.
   *
   * The wizard needs it because `onboarded` goes true the moment cubes land, which is *mid*
   * sequence: without this the wizard would unmount between cubes and describe, taking the queue
   * with it, and views would never run.
   */
  onRunning?: (running: boolean) => void;
}) {
  const [catalog, setCatalog] = useState<TableCache | null>(null);
  const [enrich, setEnrich] = useState(true);
  const [queue, setQueue] = useState<StageName[]>([]);
  const [error, setError] = useState("");
  const [failed, setFailed] = useState<{ stage: string; message: string } | null>(null);
  // Which job's ending has already been handled, so the effect below fires once per job rather
  // than on every render after one finishes.
  const settled = useRef("");
  const asked = useRef(false);

  const running = Boolean(job && !job.done);

  // Reported up rather than derived there: only this component knows about the queue, and the
  // gap between one stage ending and the next starting is exactly when the wizard must not go.
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

  // Read the database on arrival. `asked` rather than a check on `catalog`, because a database
  // with no tables reads successfully and would otherwise be re-read forever.
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

  // One job has ended: re-read the catalog, then start the next stage or record the failure.
  useEffect(() => {
    if (!job?.done || settled.current === job.id) return;
    settled.current = job.id ?? "";
    const stage = job.steps?.[0] ?? "";

    void api.tables().then(setCatalog).catch(() => {});

    if (job.error) {
      // Nothing queued after a failure. Cubes failing means there is no model; describe or views
      // failing means there is one and it is missing its prose, which is a different sentence.
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
        // A read that failed leaves nothing to pick from, and the picker's own Re-read is inside
        // the picker — so without this the only way out of a failed first read is a reload.
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
          {/* Said where the decision is made, because "why is my model just tables" is otherwise
              answered by reading the code. */}
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
