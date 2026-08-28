import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../api";
import { applyEvent, buildTranscript } from "../events";
import type { Item, RunDetail, RunSummary } from "../types";

import { Badge } from "./Badge";
import { Button } from "./Button";
import { Empty } from "./Empty";
import { Transcript } from "./Transcript";

/**
 * A flow's runs: starting one, stepping it through its gates, and reading what each phase made.
 *
 * **A run is not a request.** `advance` streams while phases execute and returns at the next gate,
 * so continuing is another call — which is why the button changes its own name rather than the
 * page having a separate "resume". Closing the tab loses the stream and never the run: every
 * status shown here comes from `run.json`, re-read after each advance.
 *
 * **The gate is the point of the whole feature.** When a run is paused, exactly one phase's output
 * is editable — the one just finished — and the server decides which by sending `editable`. This
 * component does not compute it: "may I edit this" is a question about which phase has already
 * been read by a later one, and the browser is the wrong place to answer it.
 *
 * A failed phase is left as it is rather than retried. Re-running is a button, because a phase
 * that failed for want of a missing input will fail again until someone changes something.
 */

const STATUS: Record<string, string> = {
  pending: "waiting",
  running: "running",
  done: "done",
  failed: "failed",
  stale: "stale",
};

export function FlowRuns({ flow, runnable }: { flow: string; runnable: boolean }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [openId, setOpenId] = useState<string>("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Which artifact is open, and the text as it is being edited.
  const [openFile, setOpenFile] = useState("");
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await api.listRuns(flow));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [flow]);

  const openRun = useCallback(
    async (id: string) => {
      setOpenId(id);
      setOpenFile("");
      setError("");
      try {
        const [found, events] = await Promise.all([
          api.runDetail(flow, id),
          api.runEvents(flow, id),
        ]);
        setDetail(found);
        setItems(buildTranscript(events));
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [flow],
  );

  useEffect(() => {
    setOpenId("");
    setDetail(null);
    setItems([]);
    void refreshRuns();
  }, [flow, refreshRuns]);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [items]);

  const start = async () => {
    setError("");
    try {
      const made = await api.beginRun(flow, brief);
      setBrief("");
      await refreshRuns();
      await openRun(made.run_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const advance = async () => {
    if (!openId || busy) return;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    abort.current = controller;
    try {
      for await (const event of api.advanceRun(flow, openId, controller.signal)) {
        setItems((prev) => applyEvent(prev, event));
      }
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      setBusy(false);
      abort.current = null;
      // The stream said what happened; `run.json` says where the run *is*, and only it knows
      // which phase is now editable.
      await openRun(openId);
      await refreshRuns();
    }
  };

  const stop = async () => {
    if (openId) await api.stopRun(flow, openId);
    abort.current?.abort();
    setBusy(false);
  };

  const showArtifact = async (path: string) => {
    setOpenFile(path);
    setError("");
    try {
      const body = await api.runArtifact(flow, openId, path);
      setDraft(body);
      setSaved(body);
    } catch (e) {
      setError((e as Error).message);
      setDraft("");
      setSaved("");
    }
  };

  const saveArtifact = async () => {
    try {
      await api.writeRunArtifact(flow, openId, openFile, draft);
      setSaved(draft);
      await openRun(openId);
      await showArtifact(openFile);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const editablePhase = detail?.editable ?? "";
  const phaseOfOpenFile = openFile.split("/")[0];
  const canEdit = Boolean(openFile) && phaseOfOpenFile === editablePhase;
  const label = !detail
    ? "Run"
    : detail.status === "paused"
      ? `Continue to ${detail.current}`
      : detail.status === "running"
        ? `Run ${detail.current}`
        : "";

  return (
    <div className="flow-runs">
      <div className="flow-runs-side">
        <div className="flow-start">
          <label htmlFor="flow-brief">Brief</label>
          {/* Free text, and the only thing that differs between two runs of one flow. A form of
              typed parameters here would be a workflow builder's parameter list by another name. */}
          <textarea
            id="flow-brief"
            rows={3}
            value={brief}
            placeholder="What is this run for? e.g. focus on the EU region"
            onChange={(e) => setBrief(e.target.value)}
            disabled={!runnable}
          />
          <Button variant="primary" onClick={() => void start()} disabled={!runnable}>
            Start a run
          </Button>
          {!runnable && (
            <p className="tree-blurb">
              This flow will not run yet. The Edit tab says what is missing.
            </p>
          )}
        </div>

        <ul className="nav-list">
          {runs.map((run) => (
            <li key={run.id}>
              <button
                className={`nav-row ${openId === run.id ? "active" : ""}`}
                onClick={() => void openRun(run.id)}
              >
                <span className="nav-row-name">{run.id}</span>
                <span className="nav-row-sub">
                  {run.status}
                  {run.waiting === "gate" ? " · at a gate" : ""}
                </span>
              </button>
            </li>
          ))}
          {runs.length === 0 && (
            <li>
              <Empty tight>No runs yet.</Empty>
            </li>
          )}
        </ul>
      </div>

      <div className="flow-runs-main">
        {error && <div className="editor-note bad">{error}</div>}

        {!detail ? (
          <Empty>
            {runs.length ? "Pick a run on the left." : "Start a run to see it step through."}
          </Empty>
        ) : (
          <>
            <div className="flow-run-head">
              <h3>{detail.id}</h3>
              <Badge>{detail.status}</Badge>
              {detail.waiting === "gate" && <span className="flow-gate">at a gate</span>}
              {label &&
                (busy ? (
                  <Button onClick={() => void stop()}>Stop</Button>
                ) : (
                  <Button variant="primary" onClick={() => void advance()}>
                    {label}
                  </Button>
                ))}
            </div>

            {detail.brief && <p className="flow-brief">{detail.brief}</p>}
            {detail.error && <div className="editor-note bad">{detail.error}</div>}

            <ol className="flow-phases">
              {detail.phases.map((phase) => (
                <li key={phase.dir_name} className={`flow-phase ${phase.status}`}>
                  <div className="flow-phase-head">
                    <strong>{phase.dir_name}</strong>
                    <Badge>{STATUS[phase.status] ?? phase.status}</Badge>
                    {phase.dir_name === editablePhase && (
                      <span className="flow-gate">editable now</span>
                    )}
                    <Button
                      onClick={async () => {
                        try {
                          await api.rerunPhase(flow, detail.id, phase.dir_name);
                          await openRun(detail.id);
                        } catch (e) {
                          setError((e as Error).message);
                        }
                      }}
                      disabled={busy}
                    >
                      Re-run from here
                    </Button>
                  </div>
                  {phase.error && <div className="flow-phase-error">{phase.error}</div>}
                  <div className="flow-artifacts">
                    {phase.output.map((name) => (
                      <button
                        key={name}
                        className={`flow-artifact ${openFile === `${phase.dir_name}/output/${name}` ? "on" : ""}`}
                        onClick={() => void showArtifact(`${phase.dir_name}/output/${name}`)}
                      >
                        {name}
                      </button>
                    ))}
                    {/* Named, not opened. `scratch/` is what a script wrote without declaring it,
                        kept because a chart nobody declared is still worth having — but it is not
                        what the next phase reads, so it does not get the same affordance. */}
                    {phase.scratch.length > 0 && (
                      <span className="flow-scratch" title={phase.scratch.join("\n")}>
                        +{phase.scratch.length} undeclared
                      </span>
                    )}
                    {phase.output.length === 0 && phase.status === "done" && (
                      <span className="muted">produced nothing</span>
                    )}
                  </div>
                </li>
              ))}
            </ol>

            {openFile && (
              <div className="flow-file">
                <div className="flow-file-head">
                  <strong>{openFile}</strong>
                  {canEdit ? (
                    <Button
                      variant="primary"
                      onClick={() => void saveArtifact()}
                      disabled={draft === saved}
                    >
                      {draft === saved ? "Saved" : "Save before the next phase"}
                    </Button>
                  ) : (
                    <span className="muted">
                      read-only — only {editablePhase || "a phase at a gate"} may be edited
                    </span>
                  )}
                </div>
                <textarea
                  className="flow-file-body"
                  value={draft}
                  readOnly={!canEdit}
                  onChange={(e) => setDraft(e.target.value)}
                />
              </div>
            )}

            <div className="flow-log">
              <Transcript
                items={items}
                chatId=""
                approve={(choice) => api.approveRunScript(flow, detail.id, choice)}
              />
              <div ref={tail} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
