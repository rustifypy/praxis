import { useCallback, useEffect, useRef, useState } from "react";

import { ago, useDismiss } from "../hooks";
import * as api from "../sources";
import type {
  CatalogInfo,
  Grounding,
  JobState,
  ServedModel as Model,
  SourceRow,
  StageName,
  System,
} from "../sources";
import {
  STAGE_BLURB,
  STAGE_LABEL,
  STEP_LABEL,
  healthOf,
  servingNote,
  summarise,
} from "../sources";
import { Confirm } from "./Confirm";
import { Deploy } from "./Deploy";
import { Info } from "./Info";
import { JobProgress } from "./JobProgress";
import { Menu } from "./Menu";
import { ModelEditor } from "./ModelEditor";
import { SystemBanners } from "./SystemBanners";
import { TablePicker } from "./TablePicker";

/**
 * The data page: the connected database, its pipeline, and its model.
 *
 * Three rules shape this screen. One primary action, because there is always exactly one
 * obvious next move and burying it among seven peer buttons hid it. One stage panel open at
 * a time, because the pipeline is a sequence with a position, not four independent
 * accordions. And destruction lives at the bottom behind a typed word, not beside "Test
 * connection".
 */
export function SourceDetail({
  source,
  system,
  job,
  busy,
  onRefresh,
  onEdit,
}: {
  source: SourceRow;
  system: System;
  job: JobState | null;
  busy: boolean;
  onRefresh: () => void;
  onEdit: () => void;
}) {
  const [catalog, setCatalog] = useState<CatalogInfo | null>(null);
  const [model, setModel] = useState<Model | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);
  const [grounding, setGrounding] = useState<Grounding | null>(null);
  const [loadingGrounding, setLoadingGrounding] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState(0);
  const [stage, setStage] = useState<StageName | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // Two slots, so a failed Restart reports itself next to Restart and not 400px above
  // the stage panel that had nothing to do with it.
  const [topError, setTopError] = useState("");
  const [stageError, setStageError] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  // The actions row counts as part of the pipeline: its primary button is what opens the
  // stage panel, so a click there must not be read as a click outside it.
  const actions = useRef<HTMLSpanElement>(null);
  const pipeline = useDismiss<HTMLElement>(
    stage !== null,
    () => setStage(null),
    actions,
  );

  const health = healthOf(source);
  const catalogRead = Boolean(catalog?.read);
  const upcoming = source.next_stage;

  const loadCatalog = useCallback(() => {
    void api
      .catalog()
      .then(setCatalog)
      .catch(() => setCatalog(null));
  }, []);

  const loadModel = useCallback(() => {
    setLoadingModel(true);
    void api
      .servedModel()
      .then(setModel)
      // The request itself failed, so nothing is known about the branch either — the editor
      // reads its own branch state and is not blocked by this.
      .catch((err) =>
        setModel({
          serving: false,
          error: String(err),
          entities: [],
          branch: "",
          editable: false,
          dirty: [],
          unloaded: [],
        }),
      )
      .finally(() => setLoadingModel(false));
  }, []);

  const loadGrounding = useCallback(() => {
    setLoadingGrounding(true);
    void api
      .grounding()
      .then(setGrounding)
      .catch(() => setGrounding({ root: "", exists: false, artefacts: [] }))
      .finally(() => setLoadingGrounding(false));
  }, []);

  // The catalog is cached in analyst.db and reads no database, so fetching it on arrival
  // is cheap — and the stepper cannot draw the Read stage without it.
  useEffect(() => {
    loadCatalog();
  }, [loadCatalog, job?.done]);

  // Four small files off local disk, and unlike the model it does not need the container
  // to be up — grounding is on this machine, not inside Cube.
  useEffect(() => {
    loadGrounding();
  }, [loadGrounding]);

  useEffect(() => {
    if (source.deployed) loadModel();
    else setModel(null);
  }, [source.deployed, job?.done, loadModel]);

  // The exception to nothing being open on arrival: a failure must not hide behind a closed
  // panel. Deliberately keyed on the job rather than on `source`, which is a new object after
  // every refresh and would reopen the panel each time — including right after someone closed
  // it.
  useEffect(() => {
    if (!job?.done || !job.error) return;
    const failed = source.stages.find((s) => s.state === "error");
    if (failed) setStage(failed.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.done, job?.error]);

  const guard = async (
    work: () => Promise<unknown>,
    where: "top" | "stage" = "top",
  ) => {
    setTopError("");
    setStageError("");
    try {
      await work();
      onRefresh();
    } catch (err) {
      const message = String(err instanceof Error ? err.message : err);
      (where === "top" ? setTopError : setStageError)(message);
    }
  };

  const test = async () => {
    setChecking(true);
    try {
      await guard(() => api.checkNow());
      setCheckedAt(Date.now());
    } finally {
      setChecking(false);
    }
  };

  const runStage = (name: StageName) =>
    void guard(() => api.runAction(name), "stage");

  const selected = stage;
  // The server's rail, not a second reading of `steps` — `steps.action` enforces the same
  // ordering rule and ships its answer, so this cannot explain a different one.
  const current = source.stages.find((s) => s.name === selected);
  const blocker = current?.blocked_by
    ? `Run ${STAGE_LABEL[current.blocked_by]} first`
    : "";
  const record =
    selected && selected !== "read" ? source.steps[selected] : undefined;
  // Server-computed: the same rule that performs the deletion, so a warning cannot
  // promise something different from what happens.
  const discards = (
    selected && selected !== "read"
      ? (source.invalidates?.[selected] ?? [])
      : []
  ).map((later) => STEP_LABEL[later]);

  return (
    <div className="detail">
      <header className="detail-head">
        <span
          className={`health-dot ${health.tone}`}
          title={source.health.status}
        />
        <h1>{source.label}</h1>
        <span className="detail-what">
          {source.driver}
          {` · ${servingNote(source)}`}
          {checkedAt > 0 && ` · checked ${ago(checkedAt / 1000)}`}
        </span>

        <span className="detail-primary" ref={actions}>
          {upcoming ? (
            <button
              className="primary"
              disabled={
                busy ||
                Boolean(
                  source.stages.find((s) => s.name === upcoming)?.blocked_by,
                )
              }
              onClick={() => {
                setStage(upcoming);
                // Cubes is the one stage a button cannot start: a person picks the tables
                // first. Send them to the picker rather than firing a job.
                if (upcoming === "cubes") {
                  panel.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "center",
                  });
                } else {
                  runStage(upcoming);
                }
              }}
            >
              {upcoming === "cubes" ? "Choose tables ↓" : STAGE_LABEL[upcoming]}
            </button>
          ) : (
            <button
              className="primary"
              disabled={checking}
              onClick={() => void test()}
            >
              {checking ? "Testing…" : "Test connection"}
            </button>
          )}

          {/* No link to Cube. Its port is not published — every call goes through the store,
              which is what makes that the one authenticated door — so a link would be a browser
              error page rather than an answer. */}
          {/* No lifecycle verbs. A Cube may be running anywhere — this portal's own container
              is one case of that, not the shape of the feature — so starting and stopping it
              belongs to whoever runs it, and everything here works through its URL. */}
          <Menu
            items={[
              {
                label: checking ? "Testing…" : "Test connection",
                onSelect: () => void test(),
              },
              {
                label: "Edit connection",
                onSelect: onEdit,
                disabled: busy ? "a job is running" : undefined,
              },
              {
                label: "Disconnect",
                danger: true,
                onSelect: () => setConfirmRemove(true),
                disabled: busy ? "a job is running" : undefined,
              },
            ]}
          />
        </span>
      </header>

      {source.description && (
        <p className="detail-desc">{source.description}</p>
      )}

      <SystemBanners system={system} />

      {topError && <p className="banner bad">{topError}</p>}
      {health.tone === "bad" && (
        <p className="banner bad">
          Cube is answering but the database is not reachable from it:{" "}
          {health.label}. The credentials are in the deployment's own
          environment file, so fixing them means{" "}
          <button className="link" onClick={onEdit}>
            editing the connection
          </button>{" "}
          here and re-running the compose file with the new one — a container's
          environment cannot be changed in place.
        </p>
      )}
      {/* Before the rail, because the rail is what it invalidates: every stage reads "todo"
          when the model cannot be read, and that is not the same as nothing being built. */}
      {source.store_error && (
        <p className="banner bad">
          The model store at{" "}
          <code>{source.store_url || "(none configured)"}</code> is not
          answering, so the stages below cannot say what has been built:{" "}
          {source.store_error}
        </p>
      )}

      {/* Server-computed, from the model store rather than from `steps`: an adopted model has
          no recorded runs at all, so a banner derived from what this portal did would say
          nothing about a Cube serving a dozen working cubes. */}
      {source.adopted && (
        <p className="banner">
          Cube is serving a model this portal did not build, so every stage
          below reads as not run. Re-running one replaces the whole model — the
          store keeps what is there now as a commit, and nothing is served until
          Cube has compiled the replacement.
        </p>
      )}

      {job && <JobProgress job={job} onCancel={() => void api.cancelJob()} />}

      <Deploy source={source} onSaved={onRefresh} />

      <section className="detail-panel" ref={pipeline}>
        <div className="panel-head">
          <h3>Pipeline</h3>
          <Info label="About the pipeline">
            Four stages, each runnable and re-runnable on its own — what one
            needs from the last is stored rather than held in memory, so they
            can run minutes or days apart. Click a stage to see what it produced
            and to run it again. A stage that rebuilds the model commits it to a
            candidate first and asks Cube about it there, so a model Cube
            refuses never replaces the one being served.
          </Info>
        </div>

        <ol className="stepper">
          {source.stages.map(({ name, state, note }, index) => {
            return (
              <li
                key={name}
                className={`step ${state} ${selected === name ? "on" : ""}`}
              >
                <button
                  onClick={() => setStage(selected === name ? null : name)}
                  aria-expanded={selected === name}
                >
                  <span className={`step-tick ${state}`} aria-hidden>
                    {state === "done"
                      ? "✓"
                      : state === "error"
                        ? "✕"
                        : index + 1}
                  </span>
                  <span className="step-name">{STAGE_LABEL[name]}</span>
                  {/* "9 of 11 described" — a tick alone would claim the whole model is
                      described, and nobody re-describes all of it to finish the last two. */}
                  {note && <span className="step-note">{note}</span>}
                </button>
              </li>
            );
          })}
        </ol>

        {selected && (
          <div className="stage-panel" ref={panel}>
            <div className="stage-panel-head">
              <h4>{STAGE_LABEL[selected]}</h4>
              {record?.finished_at && (
                <span className="stage-when">{ago(record.finished_at)}</span>
              )}
              <Info label={`About ${STAGE_LABEL[selected]}`}>
                {STAGE_BLURB[selected]}
              </Info>
              <button
                className="stage-close"
                onClick={() => setStage(null)}
                aria-label="Close"
              >
                ×
              </button>
            </div>

            {record?.status === "error" ? (
              <p className="banner bad">{record.error || "failed"}</p>
            ) : (
              <p className="stage-says">
                {selected === "read"
                  ? catalogRead
                    ? `${catalog?.schemas.length ?? 0} schema${catalog?.schemas.length === 1 ? "" : "s"}, ${catalog?.schemas.reduce((n, s) => n + s.tables.length, 0) ?? 0} tables`
                    : "Not read yet."
                  : summarise(selected, record) || "Not run yet."}
              </p>
            )}

            {stageError && <p className="banner bad">{stageError}</p>}
            {blocker && <p className="stage-hint">{blocker}.</p>}

            {selected === "cubes" && catalog?.truncated && (
              <p className="banner warn">
                The catalog read hit Cube's row limit, so some keys or
                statistics are missing — check the joins before trusting a wide
                schema.
              </p>
            )}
            {selected === "read" &&
              catalog?.notes.map((note) => (
                <p key={note} className="banner">
                  {note}
                </p>
              ))}

            {selected === "cubes" && catalogRead && catalog ? (
              <TablePicker
                catalog={catalog}
                busy={busy || Boolean(blocker)}
                discards={discards}
                onRun={(schema, tables) =>
                  void guard(
                    () =>
                      api.runAction("cubes", { schema_name: schema, tables }),
                    "stage",
                  )
                }
                onReread={() =>
                  void guard(() => api.runAction("read"), "stage")
                }
              />
            ) : (
              <div className="stage-cta">
                <button
                  className="primary"
                  disabled={busy || Boolean(blocker)}
                  title={blocker || undefined}
                  onClick={() => runStage(selected)}
                >
                  {current?.state === "done"
                    ? `Run ${STAGE_LABEL[selected]} again`
                    : STAGE_LABEL[selected]}
                </button>
                {discards.length > 0 && (
                  <span className="stage-hint">
                    Re-running this replaces the whole model, discarding{" "}
                    {discards.join(" and ")}. What is there now stays in the
                    store as a commit.
                  </span>
                )}
              </div>
            )}

            {record?.notes?.length ? (
              <ul className="stage-notes">
                {record.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            ) : null}

            {record?.errors && Object.keys(record.errors).length > 0 && (
              <ul className="stage-notes bad">
                {Object.entries(record.errors).map(([member, message]) => (
                  <li key={member}>
                    <code>{member}</code> {message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>

      <section className="detail-panel">
        <div className="panel-head">
          <h3>Model</h3>
          <Info label="About the model panel">
            Everything the agent brings to a question, and — on a branch — where
            you change it. The list is read live from Cube's{" "}
            <code>/v1/meta</code> and parsed by the same code the agent queries
            through, so it is what a question can actually reach rather than
            what the YAML on disk says. In dev mode Cube watches these files, so
            a save is live at once — which is why what you may save is gated
            rather than what you may load, and why a file that will not compile
            is refused before it is written. Grounding is the markdown handed to
            the agent <em>before</em> a question is, and it is re-read every
            turn.
          </Info>
        </div>
        <ModelEditor
          running={source.deployed}
          model={model}
          grounding={grounding}
          loading={loadingModel || loadingGrounding}
          onReload={() => {
            loadModel();
            loadGrounding();
          }}
        />
      </section>

      {confirmRemove && (
        <Confirm
          word="disconnect"
          title="Disconnect this database?"
          confirmLabel="Disconnect"
          onCancel={() => setConfirmRemove(false)}
          onConfirm={() => {
            setConfirmRemove(false);
            void guard(() => api.remove());
          }}
        >
          <p>
            This removes the stored credentials and the two addresses.{" "}
            <strong>Nothing is stopped</strong> — the deployment is not run from
            here, so it keeps running until you stop it yourself.
          </p>
          <p>
            The model stays in the store, commits and all. This forgets how to
            reach the database, not what was built on it.
          </p>
        </Confirm>
      )}
    </div>
  );
}
