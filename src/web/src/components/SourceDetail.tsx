import { useState } from "react";

import { ago } from "../hooks";
import * as api from "../sources";
import type { JobState, SourceRow } from "../sources";
import { healthOf, servingNote } from "../sources";
import { Button } from "./Button";
import { warehouseName } from "./AddSource";
import { BuildModel } from "./BuildModel";
import { Confirm } from "./Confirm";
import { Info } from "./Info";
import { Menu } from "./Menu";
import { ModelEditor } from "./ModelEditor";
import { SystemBanners } from "./SystemBanners";

/** The data page: the connected database, and its model. */
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
      {/* Only the connection scrolls: the editor needs a fixed height to give CodeMirror. */}
      <div className="detail-scroll">
      <header className="detail-head">
        <span className={`health-dot ${health.tone}`} title={source.health.status} />
        <h1>{source.label}</h1>
        <span className="detail-what">
          {warehouseName(source.driver)}
          {` · ${servingNote(source)}`}
          {checkedAt > 0 && ` · checked ${ago(checkedAt / 1000)}`}
        </span>

        <span className="detail-primary">
          <Button variant="primary" disabled={checking} onClick={() => void test()}>
            {checking ? "Testing…" : "Test connection"}
          </Button>

          {/* No link to Cube: its port is not published, only the store's. */}
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
          <Button variant="link" onClick={onEdit}>
            editing the connection
          </Button>{" "}
          here and re-running that file — a container's environment cannot be changed in place.
        </p>
      )}
      {source.store_error && (
        <p className="banner bad">
          The model store at <code>{source.store_url || "(none configured)"}</code> is not
          answering, so nothing below can say what the model holds: {source.store_error}
        </p>
      )}

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
              <Button variant="link" onClick={() => setRebuilding(false)}>
                Cancel
              </Button>
            )}
          </div>
          <BuildModel job={job} rebuilding onJob={onJob} />
        </section>
      )}

      </div>

      <ModelEditor deployed={source.deployed} onChanged={onRefresh} />

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
