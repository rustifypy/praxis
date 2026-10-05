import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../api";
import type { AgentEvent, RunDetail, RunSummary } from "../types";

import { Button } from "./Button";
import { Confirm } from "./Confirm";
import { Empty } from "./Empty";
import { RunStory } from "./RunStory";

/**
 * A flow's runs: starting one, stepping it through its gates, and reading what each phase made.
 *
 * **A run is not a request.** `advance` streams while phases execute and returns at the next gate,
 * so continuing is another call — which is why the button changes its own name rather than the
 * page having a separate "resume". Closing the tab loses the stream and never the run: every
 * status shown here comes from `run.json`.
 *
 * **What is on screen is read, not streamed.** The run's state is `run.json` and its history is
 * `events.jsonl`, and both are re-read every two seconds while a run is going. The live stream is
 * kept for the one thing that exists nowhere else — a script waiting for approval — and for
 * knowing when the advance has ended.
 *
 * **The gate is the point of the whole feature.** When a run is paused, exactly one phase's output
 * is editable — the one just finished — and the server decides which by sending `editable`. This
 * component does not compute it: "may I edit this" is a question about which phase has already
 * been read by a later one, and the browser is the wrong place to answer it.
 */

const POLL_MS = 2000;

export function FlowRuns({ flow, runnable }: { flow: string; runnable: boolean }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [openId, setOpenId] = useState<string>("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [asking, setAsking] = useState<{ script: string } | null>(null);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await api.listRuns(flow));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [flow]);

  const reload = useCallback(
    async (id: string) => {
      const [found, logged] = await Promise.all([
        api.runDetail(flow, id),
        api.runEvents(flow, id),
      ]);
      setDetail(found);
      setEvents(logged);
    },
    [flow],
  );

  const openRun = useCallback(
    async (id: string) => {
      setOpenId(id);
      setAsking(null);
      setError("");
      try {
        await reload(id);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [reload],
  );

  useEffect(() => {
    setOpenId("");
    setDetail(null);
    setEvents([]);
    void refreshRuns();
  }, [flow, refreshRuns]);

  // One advance can run several phases back to back, and the stream does not say which phase an
  // event belongs to. The files do, so they are re-read while it runs.
  useEffect(() => {
    if (!busy || !openId) return;
    const timer = setInterval(() => {
      reload(openId).catch(() => {});
      void refreshRuns();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, openId, reload, refreshRuns]);

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
        // An approval is never stored, so this stream is the only place it is seen. The step it
        // belongs to ending is what retires it.
        if (event.type === "approval") setAsking({ script: String(event.data.script ?? "") });
        if (event.type === "tool_end" && event.data.tool === "run_python") setAsking(null);
      }
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      setBusy(false);
      setAsking(null);
      abort.current = null;
      await openRun(openId);
      await refreshRuns();
    }
  };

  // Asked of the server, and the stream is left to end by itself: the phase then records that it
  // was stopped. Cutting the connection instead cancels the run mid-phase, and the only thing the
  // server can say about that is "stopped before it finished". The connection is cut only when
  // nothing on the server is listening for the stop.
  const stop = async () => {
    const said = openId ? await api.stopRun(flow, openId) : { stopped: false };
    if (!said.stopped) {
      abort.current?.abort();
      setBusy(false);
    }
  };

  const rerun = async (dirName: string) => {
    if (!detail) return;
    try {
      await api.rerunPhase(flow, detail.id, dirName);
      await openRun(detail.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const remove = async () => {
    if (!detail) return;
    setDeleting(false);
    try {
      await api.deleteRun(flow, detail.id);
      setOpenId("");
      setDetail(null);
      setEvents([]);
      await refreshRuns();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const name = (dir: string) =>
    detail?.phases.find((phase) => phase.dir_name === dir)?.plan?.title || dir;
  // "Continue" only past a Gate. A new run is paused too, before phase one, and a run whose
  // stream died says running — both are started with "Run".
  const action = !detail
    ? ""
    : detail.status === "paused" && detail.waiting === "gate"
      ? `Continue to ${name(detail.current)}`
      : detail.status === "paused" || detail.status === "running"
        ? `Run ${name(detail.current)}`
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
                <span className="nav-row-name">{started(run.started_at)}</span>
                <span className="nav-row-sub">
                  {run.status === "paused" && run.waiting === "gate"
                    ? "waiting for review"
                    : run.status}
                  {" · "}
                  {run.id.slice(-6)}
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
          <RunStory
            flow={flow}
            detail={detail}
            events={events}
            busy={busy}
            asking={asking}
            action={action}
            onAdvance={() => void advance()}
            onStop={() => void stop()}
            onRerun={(dirName) => void rerun(dirName)}
            onSaved={() => void openRun(detail.id)}
            onDelete={() => setDeleting(true)}
          />
        )}
        {deleting && detail && (
          <Confirm
            word="delete"
            title="Delete this run?"
            confirmLabel="Delete run"
            onCancel={() => setDeleting(false)}
            onConfirm={() => void remove()}
          >
            <p>
              The run from {started(detail.started_at)} and everything it produced — every phase's
              outputs, its log and its scripts — are removed from <code>runs/{detail.id}</code>.
              This cannot be undone. The flow itself and its other runs are not touched.
            </p>
          </Confirm>
        )}
      </div>
    </div>
  );
}

function started(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
