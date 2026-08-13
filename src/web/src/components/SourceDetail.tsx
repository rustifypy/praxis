import { useCallback, useEffect, useRef, useState } from "react";

import { ago, useDismiss } from "../hooks";
import * as api from "../sources";
import type {
  CatalogInfo,
  JobState,
  ServedModel as Model,
  SourceGrounding,
  SourceRow,
  StageName,
} from "../sources";
import {
  STAGE_BLURB,
  STAGE_LABEL,
  STEP_LABEL,
  healthOf,
  servingSummary,
  summarise,
} from "../sources";
import { ConfirmName } from "./ConfirmName";
import { Info } from "./Info";
import { JobProgress } from "./JobProgress";
import { Menu } from "./Menu";
import { ModelEditor } from "./ModelEditor";
import { TablePicker } from "./TablePicker";

/**
 * One source, on its own page.
 *
 * Three rules shape this screen. One primary action, because a source always has exactly
 * one obvious next move and burying it among seven peer buttons hid it. One stage panel
 * open at a time, because the pipeline is a sequence with a position, not four
 * independent accordions. And destruction lives at the bottom behind a typed name, not
 * beside "Test connection".
 */
export function SourceDetail({
  source,
  job,
  busy,
  onRefresh,
  onEdit,
  onBack,
}: {
  source: SourceRow;
  job: JobState | null;
  busy: boolean;
  onRefresh: () => void;
  onEdit: () => void;
  onBack: () => void;
}) {
  const [catalog, setCatalog] = useState<CatalogInfo | null>(null);
  const [model, setModel] = useState<Model | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);
  const [grounding, setGrounding] = useState<SourceGrounding | null>(null);
  const [loadingGrounding, setLoadingGrounding] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState(0);
  const [stage, setStage] = useState<StageName | null>(null);
  const [logs, setLogs] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  // Two slots, so a failed Restart reports itself next to Restart and not 400px above
  // the stage panel that had nothing to do with it.
  const [topError, setTopError] = useState("");
  const [stageError, setStageError] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  // The actions row counts as part of the pipeline: its primary button is what opens the
  // stage panel, so a click there must not be read as a click outside it.
  const actions = useRef<HTMLSpanElement>(null);
  const pipeline = useDismiss<HTMLElement>(stage !== null, () => setStage(null), actions);

  const health = healthOf(source);
  const catalogRead = Boolean(catalog?.read);
  const upcoming = source.next_stage;

  const loadCatalog = useCallback(() => {
    void api
      .catalog(source.name)
      .then(setCatalog)
      .catch(() => setCatalog(null));
  }, [source.name]);

  const loadModel = useCallback(() => {
    setLoadingModel(true);
    void api
      .servedModel(source.name)
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
  }, [source.name]);

  const loadGrounding = useCallback(() => {
    setLoadingGrounding(true);
    void api
      .sourceGrounding(source.name)
      .then(setGrounding)
      .catch(() => setGrounding({ root: "", exists: false, artefacts: [] }))
      .finally(() => setLoadingGrounding(false));
  }, [source.name]);

  // The catalog is cached in sources.db and reads no database, so fetching it on arrival
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
    if (source.running) loadModel();
    else setModel(null);
  }, [source.running, job?.done, loadModel]);

  // Nothing is open until a stage is clicked, and moving to another source closes it
  // again. The stepper already says where the pipeline stands; a panel volunteering its
  // contents put a wall of stage detail under every arrival.
  useEffect(() => {
    setStage(null);
  }, [source.name]);

  // The exception: a failure must not hide behind a closed panel. Deliberately keyed on
  // the job rather than on `source`, which is a new object after every refresh and would
  // reopen the panel each time — including right after someone closed it.
  useEffect(() => {
    if (!job?.done || !job.error) return;
    const failed = source.stages.find((s) => s.state === "error");
    if (failed) setStage(failed.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.done, job?.error]);

  const guard = async (work: () => Promise<unknown>, where: "top" | "stage" = "top") => {
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
      await guard(() => api.checkNow(source.name));
      setCheckedAt(Date.now());
    } finally {
      setChecking(false);
    }
  };

  const showLogs = () => {
    if (logs !== null) {
      setLogs(null);
      return;
    }
    setLogs("Loading…");
    void api.logs(source.name).then((r) => setLogs(r.logs || "(no output)"));
  };

  const runStage = (name: StageName) =>
    void guard(() => api.runAction(source.name, name), "stage");

  const selected = stage;
  // The server's rail, not a second reading of `steps` — `run.action` enforces the same
  // ordering rule and ships its answer, so this cannot explain a different one.
  const current = source.stages.find((s) => s.name === selected);
  const blocker = current?.blocked_by ? `Run ${STAGE_LABEL[current.blocked_by]} first` : "";
  const record = selected && selected !== "read" ? source.steps[selected] : undefined;
  // Server-computed: the same rule that performs the deletion, so a warning cannot
  // promise something different from what happens.
  const discards = (
    selected && selected !== "read" ? (source.invalidates?.[selected] ?? []) : []
  ).map((later) => STEP_LABEL[later]);

  return (
    <div className="detail">
      <nav className="crumbs">
        <button onClick={onBack}>Sources</button>
        <span aria-hidden>/</span>
        <span>{source.name}</span>
      </nav>

      <header className="detail-head">
        <span className={`health-dot ${health.tone}`} title={source.health.status} />
        <h1>{source.name}</h1>
        {source.active ? (
          <span className="badge accent">active</span>
        ) : (
          <span className="badge">not connected</span>
        )}
        <span className="detail-what">
          {source.driver} · :{source.port}
          {source.serving && ` · ${servingSummary(source.serving)}`}
          {checkedAt > 0 && ` · checked ${ago(checkedAt / 1000)}`}
        </span>

        <span className="detail-primary" ref={actions}>
          {upcoming ? (
            <button
              className="primary"
              disabled={busy || Boolean(source.stages.find((s) => s.name === upcoming)?.blocked_by)}
              onClick={() => {
                setStage(upcoming);
                // Cubes is the one stage a button cannot start: a person picks the tables
                // first. Send them to the picker rather than firing a job.
                if (upcoming === "cubes") {
                  panel.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                } else {
                  runStage(upcoming);
                }
              }}
            >
              {upcoming === "cubes" ? "Choose tables ↓" : STAGE_LABEL[upcoming]}
            </button>
          ) : (
            <button className="primary" disabled={checking} onClick={() => void test()}>
              {checking ? "Testing…" : "Test connection"}
            </button>
          )}

          {/* The container's own Cube. In dev mode this is the playground; otherwise it
              is the API root. Rendered only while it is up — a link to a stopped
              container is a browser error page, not an answer. */}
          {source.running && (
            <a
              className="cube-link"
              href={source.url}
              target="_blank"
              rel="noreferrer"
              title={`Open this source's Cube at ${source.url}`}
            >
              Cube <span aria-hidden>↗</span>
            </a>
          )}

          <Menu
            items={[
              {
                label: checking ? "Testing…" : "Test connection",
                onSelect: () => void test(),
                disabled: source.running ? undefined : "the container is not running",
              },
              source.running
                ? { label: "Restart", onSelect: () => void guard(() => api.control(source.name, "restart")) }
                : {
                    label: source.container_state === "missing" ? "Create container" : "Start",
                    onSelect: () =>
                      void guard(() =>
                        source.container_state === "missing"
                          ? api.runAction(source.name, "connect")
                          : api.control(source.name, "start"),
                      ),
                  },
              {
                label: "Stop",
                onSelect: () => void guard(() => api.control(source.name, "stop")),
                disabled: source.running ? undefined : "already stopped",
              },
              { label: logs === null ? "Container output" : "Hide output", onSelect: showLogs },
              {
                label: "Edit connection",
                onSelect: onEdit,
                disabled: busy ? "a job is running" : undefined,
              },
              {
                label: "Remove source",
                danger: true,
                onSelect: () => setConfirmRemove(true),
                disabled: source.active
                  ? "point CUBE_URL elsewhere first — the portal queries this one"
                  : busy
                    ? "a job is running"
                    : undefined,
              },
            ]}
          />
        </span>
      </header>

      {source.description && <p className="detail-desc">{source.description}</p>}

      {topError && <p className="banner bad">{topError}</p>}
      {health.tone === "bad" && (
        <p className="banner bad">
          The container is up but the database is unreachable: {health.label}.{" "}
          <button className="link" onClick={onEdit}>
            Edit the credentials
          </button>{" "}
          or read the container output.
        </p>
      )}
      {source.serving && source.serving.views.length > 0 && !source.steps.views?.status && (
        <p className="banner">
          Cube is serving {source.serving.views.join(", ")}, but this portal did not build
          them — the stages below only record what it ran, and re-running one rewrites every
          model file.
        </p>
      )}

      {logs !== null && (
        <pre className="source-logs">{logs}</pre>
      )}

      {job && <JobProgress job={job} onCancel={() => void api.cancelJob()} />}

      <section className="detail-panel" ref={pipeline}>
        <div className="panel-head">
          <h3>Pipeline</h3>
          <Info label="About the pipeline">
            Five stages, each runnable and re-runnable on its own — what one needs from the
            last is stored on the source rather than held in memory, so they can run minutes
            or days apart. Click a stage to see what it produced and to run it again.
          </Info>
        </div>

        <ol className="stepper">
          {source.stages.map(({ name, state }, index) => {
            return (
              <li key={name} className={`step ${state} ${selected === name ? "on" : ""}`}>
                <button
                  onClick={() => setStage(selected === name ? null : name)}
                  aria-expanded={selected === name}
                >
                  <span className={`step-tick ${state}`} aria-hidden>
                    {state === "done" ? "✓" : state === "error" ? "✕" : index + 1}
                  </span>
                  <span className="step-name">{STAGE_LABEL[name]}</span>
                </button>
              </li>
            );
          })}
        </ol>

        {selected && (
        <div className="stage-panel" ref={panel}>
          <div className="stage-panel-head">
            <h4>{STAGE_LABEL[selected]}</h4>
            {record?.finished_at && <span className="stage-when">{ago(record.finished_at)}</span>}
            <Info label={`About ${STAGE_LABEL[selected]}`}>{STAGE_BLURB[selected]}</Info>
            <button className="stage-close" onClick={() => setStage(null)} aria-label="Close">
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
              The catalog read hit Cube's row limit, so some keys or statistics are missing —
              check the joins before trusting a wide schema.
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
              name={source.name}
              catalog={catalog}
              busy={busy || Boolean(blocker)}
              discards={discards}
              onRun={(schema, tables) =>
                void guard(
                  () => api.runAction(source.name, "cubes", { schema_name: schema, tables }),
                  "stage",
                )
              }
              onReread={() => void guard(() => api.runAction(source.name, "read"), "stage")}
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
                  Re-running this rewrites the model files, discarding {discards.join(" and ")}.
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
            Everything the agent brings to a question, and — on a branch — where you change
            it. The list is read live from this source's <code>/v1/meta</code> and parsed by
            the same code the agent queries through, so it is what a question can actually
            reach rather than what the YAML on disk says. In dev mode Cube watches these
            files, so a save is live at once — which is why what you may save is gated rather
            than what you may load, and why a file that will not compile is refused before it
            is written. Grounding is the markdown this source hands the agent <em>before</em> a
            question does, and it is re-read every turn.
          </Info>
        </div>
        <ModelEditor
          name={source.name}
          running={source.running}
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
        <ConfirmName
          name={source.name}
          title={`Remove ${source.name}?`}
          confirmLabel="Remove source"
          onCancel={() => setConfirmRemove(false)}
          onConfirm={() => {
            setConfirmRemove(false);
            void guard(() => api.remove(source.name)).then(onBack);
          }}
        >
          <p>
            This stops and deletes <code>{source.container}</code>, removes the row and its
            stored credentials, and deletes the model directory{" "}
            <code>cube/sources/{source.name}/</code>.
          </p>
          <p>
            Rebuilding it costs one model call per table. If that directory is not committed,
            there is no way back.
          </p>
        </ConfirmName>
      )}
    </div>
  );
}
