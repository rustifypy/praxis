import { useState } from "react";

import { ago } from "../hooks";
import * as api from "../sources";
import type { JobState, SourceRow } from "../sources";
import { healthOf, servingNote } from "../sources";
import { warehouseName } from "./AddSource";
import { BuildModel } from "./BuildModel";
import { Confirm } from "./Confirm";
import { Info } from "./Info";
import { Menu } from "./Menu";
import { ModelEditor } from "./ModelEditor";
import { SystemBanners } from "./SystemBanners";

/**
 * The data page: the connected database, and its model.
 *
 * **There is no pipeline on this screen.** Building the model is the last step of onboarding, so
 * by the time anyone arrives here it exists — and a four-stage stepper offering to re-run
 * individual steps was an admin surface for something that happens once. What is left is
 * Rebuild: the same picker, behind a typed confirmation, running every stage in order.
 *
 * **There is no deployment panel either.** The addresses are pasted during onboarding and the
 * portal starts nothing, so there is nothing here to start, stop, re-point or rotate. Editing the
 * credentials stays, because a wrong warehouse password is fixed by editing them and re-running
 * the compose file — which the form says, since a container's environment cannot be changed in
 * place.
 *
 * So the page is two things: what you are connected to, and the model on top of it.
 */
export function SourceDetail({
  source,
  job,
  onJob,
  onRefresh,
  onEdit,
}: {
  source: SourceRow;
  job: JobState | null;
  onJob: (job: JobState) => void;
  onRefresh: () => void;
  onEdit: () => void;
}) {
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState(0);
  const [error, setError] = useState("");
  const [rebuilding, setRebuilding] = useState(false);
  const [confirmRebuild, setConfirmRebuild] = useState(false);

  const health = healthOf(source);
  const busy = Boolean(job && !job.done);

  const test = async () => {
    setChecking(true);
    setError("");
    try {
      await api.probe();
      setCheckedAt(Date.now());
      onRefresh();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="detail">
      <header className="detail-head">
        <span className={`health-dot ${health.tone}`} title={source.health.status} />
        <h1>{source.label}</h1>
        <span className="detail-what">
          {warehouseName(source.driver)}
          {` · ${servingNote(source)}`}
          {checkedAt > 0 && ` · checked ${ago(checkedAt / 1000)}`}
        </span>

        <span className="detail-primary">
          <button className="primary" disabled={checking} onClick={() => void test()}>
            {checking ? "Testing…" : "Test connection"}
          </button>

          {/* No link to Cube. Its port is not published — every call goes through the store,
              which is what makes that the one authenticated door — so a link would be a browser
              error page rather than an answer. */}
          {/* No lifecycle verbs. Every service runs wherever its owner put it, so starting and
              stopping belongs to them, and everything here works through an address. */}
          <Menu
            items={[
              {
                label: "Edit connection",
                onSelect: onEdit,
                disabled: busy ? "a job is running" : undefined,
              },
              {
                label: "Rebuild the model",
                onSelect: () => setConfirmRebuild(true),
                disabled: busy ? "a job is running" : undefined,
              },
            ]}
          />
        </span>
      </header>

      {source.description && <p className="detail-desc">{source.description}</p>}

      <SystemBanners source={source} />

      {error && <p className="banner bad">{error}</p>}
      {health.tone === "bad" && (
        <p className="banner bad">
          Cube is answering but the database is not reachable from it: {health.label}. The
          credentials are in the deployment's own compose file, so fixing them means{" "}
          <button className="link" onClick={onEdit}>
            editing the connection
          </button>{" "}
          here and re-running that file — a container's environment cannot be changed in place.
        </p>
      )}
      {source.store_error && (
        <p className="banner bad">
          The model store at <code>{source.store_url || "(none configured)"}</code> is not
          answering, so nothing below can say what the model holds: {source.store_error}
        </p>
      )}

      {/* Open only while a rebuild is being set up or is running. It is the pipeline, and the
          pipeline is not what this page is about. */}
      {(rebuilding || busy) && (
        <section className="detail-panel">
          <div className="panel-head">
            <h3>Rebuild</h3>
            <Info label="About rebuilding">
              Every stage from scratch: read the database, emit one cube per table, then
              descriptions and views if you ask for them. It lands as one commit on main, so what
              is there now stays in the store's history — but hand edits to main are overwritten,
              which is why edits belong on a branch.
            </Info>
            {!busy && (
              <button className="link" onClick={() => setRebuilding(false)}>
                Cancel
              </button>
            )}
          </div>
          <BuildModel job={job} rebuilding onJob={onJob} />
        </section>
      )}

      <section className="detail-panel">
        <div className="panel-head">
          <h3>Model</h3>
          <Info label="About the model">
            Everything the agent brings to a question, and where you change it. The file tree is
            what the model store holds for the branch you are on; the Served model tab is read
            live from Cube's <code>/v1/meta</code> and parsed by the same code the agent queries
            through, so it is what a question can actually reach rather than what the YAML says.
            Main is generated and read-only — edits go on a branch, and Cube serves every branch
            at once, so a commit is live on that branch immediately and main is untouched until
            you merge.
          </Info>
        </div>
        <ModelEditor deployed={source.deployed} onChanged={onRefresh} />
      </section>

      {confirmRebuild && (
        <Confirm
          word="rebuild"
          title="Rebuild the whole model?"
          confirmLabel="Rebuild"
          onCancel={() => setConfirmRebuild(false)}
          onConfirm={() => {
            setConfirmRebuild(false);
            setRebuilding(true);
          }}
        >
          <p>
            Every model file on main is rewritten in one commit, from the database as it is now.
            Views and descriptions are regenerated, so anything hand-written on main is replaced.
          </p>
          <p>
            The store keeps what is there now as a commit, so the diff is readable afterwards —
            and nothing stops serving in the meantime, because the new model is compiled on a
            candidate first and only lands if Cube accepts it.
          </p>
        </Confirm>
      )}
    </div>
  );
}
