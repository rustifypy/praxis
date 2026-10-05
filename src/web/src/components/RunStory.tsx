import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import * as api from "../api";
import type { FilePreview } from "../api";
import type { AgentEvent, RunDetail, RunPhase } from "../types";

import { Approval } from "./Approval";
import { Badge } from "./Badge";
import { Button } from "./Button";

/**
 * One Run, told phase by phase: what each phase is for, what it read, what it did, and what it
 * produced — with the data itself, so a person can judge a phase without opening a file.
 *
 * **Not a chat.** A Run answers in its files, and a transcript of the model's turn — its prompt,
 * its thinking, its closing remarks — is the wrong shape for reading one. The log is still
 * complete in `events.jsonl`; this renders the parts a reviewer acts on.
 *
 * **Nothing here decides state.** Which phase is running, which is at a Gate, which output may be
 * edited: all of it is `run.json`, shipped as `detail`. What this component derives is wording.
 */

const STATUS_TONE = {
  pending: "default",
  running: "accent",
  done: "ok",
  failed: "bad",
  stale: "warn",
} as const;

const STATUS_WORD = {
  pending: "not started",
  running: "running",
  done: "done",
  failed: "failed",
  stale: "stale",
};

export function RunStory({
  flow,
  detail,
  events,
  busy,
  asking,
  action,
  onAdvance,
  onStop,
  onRerun,
  onSaved,
  onDelete,
}: {
  flow: string;
  detail: RunDetail;
  events: AgentEvent[];
  busy: boolean;
  /** A script waiting on a click, from the live stream — an approval is never stored. */
  asking: { script: string } | null;
  /** The primary button's words, or "" when the run is over. */
  action: string;
  onAdvance: () => void;
  onStop: () => void;
  onRerun: (dirName: string) => void;
  onSaved: () => void;
  onDelete: () => void;
}) {
  const groups = groupByPhase(events, detail.phases);
  const done = detail.phases.filter((p) => p.status === "done").length;

  return (
    <div className="story">
      <header className="story-head">
        <div className="story-title">
          <h3>Run of {when(detail.started_at)}</h3>
          <Badge variant="pill" tone={RUN_TONE[detail.status]}>
            {detail.status === "paused" && detail.waiting === "gate" ? "waiting for review" : detail.status}
          </Badge>
          <span className="story-count">
            {done} of {detail.phases.length} phases done
          </span>
          <span className="story-spacer" />
          <Button variant="subtle" onClick={onDelete} disabled={busy}>
            Delete run
          </Button>
          {action &&
            (busy ? (
              <Button onClick={onStop}>Stop</Button>
            ) : (
              <Button variant="primary" onClick={onAdvance}>
                {action}
              </Button>
            ))}
        </div>
        <p className={`story-state ${detail.status}`}>{sentence(detail, busy)}</p>
        {detail.brief && (
          <div className="story-brief">
            <span className="story-label">Brief</span>
            <p>{detail.brief}</p>
          </div>
        )}
        <ol className="story-steps" aria-label="Phases">
          {detail.phases.map((phase, i) => (
            <li key={phase.dir_name} className={`story-step ${phase.status}`}>
              <a href={`#phase-${phase.dir_name}`}>
                <span className="story-dot" aria-hidden="true">
                  {phase.status === "done" ? "✓" : phase.status === "failed" ? "✕" : i + 1}
                </span>
                <span className="story-step-name">{phase.plan?.title || phase.dir_name}</span>
              </a>
            </li>
          ))}
        </ol>
      </header>

      {detail.phases.map((phase, i) => {
        const next = detail.phases[i + 1];
        const atThisGate =
          detail.status === "paused" && detail.waiting === "gate" && next?.dir_name === detail.current;
        return (
          <div key={phase.dir_name}>
            <PhaseSection
              flow={flow}
              runId={detail.id}
              number={i + 1}
              phase={phase}
              events={groups.get(phase.dir_name) ?? []}
              editable={detail.editable === phase.dir_name}
              live={busy && detail.current === phase.dir_name}
              asking={busy && detail.current === phase.dir_name ? asking : null}
              busy={busy}
              onRerun={() => onRerun(phase.dir_name)}
              onSaved={onSaved}
            />
            {next && phase.plan?.review === "required" && (
              <div className={`story-gate ${atThisGate ? "here" : ""}`}>
                <span className="story-gate-mark" aria-hidden="true">
                  ⏸
                </span>
                {atThisGate ? (
                  <div className="story-gate-body">
                    <strong>Paused here for your review.</strong> Read what{" "}
                    {phase.plan?.title || phase.dir_name} produced above, and edit its outputs if
                    something is wrong. The next phase reads them exactly as they are when you
                    continue.
                    {!busy && (
                      <Button variant="primary" onClick={onAdvance}>
                        Continue to {next.plan?.title || next.dir_name}
                      </Button>
                    )}
                  </div>
                ) : (
                  <div className="story-gate-body muted">Review point — the run pauses here.</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const RUN_TONE = { running: "accent", paused: "warn", done: "ok", failed: "bad" } as const;

function sentence(detail: RunDetail, busy: boolean): string {
  const name = (dir: string) => detail.phases.find((p) => p.dir_name === dir)?.plan?.title || dir;
  const failed = detail.phases.find((p) => p.status === "failed");
  if (detail.status === "done") return `Finished. All ${detail.phases.length} phases completed.`;
  if (detail.status === "failed")
    return `Stopped at ${failed ? name(failed.dir_name) : "a phase"}: ${detail.error || failed?.error || "it failed"}. Fix the cause, then use “Re-run from here” on that phase.`;
  if (busy || detail.status === "running") return `Working on ${name(detail.current)}…`;
  if (detail.waiting === "gate") {
    const at = detail.phases.findIndex((p) => p.dir_name === detail.current);
    const before = at > 0 ? detail.phases[at - 1].dir_name : "";
    return `Waiting for you. ${name(before)} is done and paused for review; ${name(detail.current)} runs when you continue.`;
  }
  return `Ready. Nothing has run yet — ${name(detail.current)} is first.`;
}

function PhaseSection({
  flow,
  runId,
  number,
  phase,
  events,
  editable,
  live,
  asking,
  busy,
  onRerun,
  onSaved,
}: {
  flow: string;
  runId: string;
  number: number;
  phase: RunPhase;
  events: AgentEvent[];
  editable: boolean;
  live: boolean;
  asking: { script: string } | null;
  busy: boolean;
  onRerun: () => void;
  onSaved: () => void;
}) {
  const plan = phase.plan;
  const steps = stepsOf(events);
  // Steps the agent improvised in a phase that came with prepared work. Not wrong — the brief may
  // have asked for something the prepared work does not do — but a reviewer should know.
  const prepared = (plan?.queries.length ?? 0) + (plan?.scripts.length ?? 0) > 0;
  const ran = phase.started_at > 0;
  const declared = plan?.outputs ?? phase.output;
  const rules = plan?.inputs.filter((input) => input.kind === "reference") ?? [];
  const material = plan?.inputs.filter((input) => input.kind === "working") ?? [];

  return (
    <section id={`phase-${phase.dir_name}`} className={`phase ${phase.status}`}>
      <div className="phase-head">
        <span className="phase-number">{number}</span>
        <div className="phase-titles">
          <h4>{plan?.title || phase.dir_name}</h4>
          <span className="phase-dir">{phase.dir_name}</span>
        </div>
        <Badge variant="pill" tone={STATUS_TONE[phase.status]}>
          {STATUS_WORD[phase.status]}
        </Badge>
        {ran && <span className="phase-time">{took(phase)}</span>}
        <span className="story-spacer" />
        {ran && (
          <Button size="sm" variant="subtle" onClick={onRerun} disabled={busy}>
            Re-run from here
          </Button>
        )}
      </div>

      {plan?.purpose && <p className="phase-purpose">{plan.purpose}</p>}
      {phase.status === "stale" && (
        <p className="phase-note warn">
          Something this phase read has changed since it ran, so its outputs may no longer follow.
          Re-run it to bring them up to date.
        </p>
      )}

      {(rules.length > 0 || material.length > 0 || plan) && (
        <dl className="phase-facts">
          {material.length > 0 && (
            <>
              <dt>Reads</dt>
              <dd>
                {material.map((input) => (
                  <span key={input.path} className="phase-chip" title={input.path}>
                    {readable(input.path)}
                  </span>
                ))}
              </dd>
            </>
          )}
          {rules.length > 0 && (
            <>
              <dt>Follows</dt>
              <dd>
                {rules.map((input) => (
                  <span key={input.path} className="phase-chip rule" title={input.path}>
                    {input.path.split("/").pop()}
                  </span>
                ))}
              </dd>
            </>
          )}
          {plan && (
            <>
              <dt>How</dt>
              <dd className="muted">{how(plan.kind, plan.tools)}</dd>
            </>
          )}
          {plan && (plan.queries.length > 0 || plan.scripts.length > 0) && (
            <>
              <dt>Prepared</dt>
              <dd>
                {plan.queries.map((name) => (
                  <span key={name} className="phase-chip rule" title={`queries/${name}`}>
                    {name}
                  </span>
                ))}
                {plan.scripts.map((name) => (
                  <span key={name} className="phase-chip rule">
                    {name}
                  </span>
                ))}
              </dd>
            </>
          )}
        </dl>
      )}

      {(steps.length > 0 || live) && (
        <div className="phase-block">
          <span className="story-label">What it did</span>
          <ol className="phase-steps">
            {steps.map((step, i) => (
              <Step key={i} step={step} prepared={prepared} />
            ))}
            {asking && (
              <li className="step asking">
                <span className="step-mark">?</span>
                <div className="step-body">
                  <div>Wants to run a script. Read it, then decide.</div>
                  <pre className="step-pre">{asking.script}</pre>
                  <Approval
                    chatId=""
                    decide={(choice) => api.approveRunScript(flow, runId, choice)}
                  />
                </div>
              </li>
            )}
            {live && !asking && (
              <li className="step working">
                <span className="spinner" />
                <div className="step-body muted">Working…</div>
              </li>
            )}
          </ol>
        </div>
      )}

      {phase.error && <div className="phase-note bad">{phase.error}</div>}

      {declared.length > 0 && (ran || phase.output.length > 0) && (
        <div className="phase-block">
          <span className="story-label">What it produced</span>
          <div className="phase-outputs">
            {declared.map((name) => (
              <OutputCard
                key={name}
                flow={flow}
                runId={runId}
                path={`${phase.dir_name}/output/${name}`}
                name={name}
                present={phase.output.includes(name)}
                mayBeEmpty={plan?.may_be_empty.includes(name) ?? false}
                editable={editable}
                version={`${phase.ended_at}`}
                pending={phase.status === "running" || phase.status === "pending"}
                onSaved={onSaved}
              />
            ))}
          </div>
          {phase.scratch.length > 0 && (
            <p className="phase-scratch">
              Also saved, but not passed on: {phase.scratch.join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function OutputCard({
  flow,
  runId,
  path,
  name,
  present,
  mayBeEmpty,
  editable,
  version,
  pending,
  onSaved,
}: {
  flow: string;
  runId: string;
  path: string;
  name: string;
  present: boolean;
  mayBeEmpty: boolean;
  editable: boolean;
  version: string;
  pending: boolean;
  onSaved: () => void;
}) {
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [failed, setFailed] = useState("");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Re-read when the phase ends again (a re-run) or the file is saved: `version` changes.
  useEffect(() => {
    if (!present) return;
    let live = true;
    api.runPreview(flow, runId, path).then(
      (found) => live && setPreview(found),
      (e) => live && setFailed((e as Error).message),
    );
    return () => {
      live = false;
    };
  }, [flow, runId, path, present, version]);

  const edit = async () => {
    try {
      setEditing(await api.runArtifact(flow, runId, path));
    } catch (e) {
      setFailed((e as Error).message);
    }
  };

  const save = async () => {
    if (editing === null) return;
    setSaving(true);
    try {
      await api.writeRunArtifact(flow, runId, path, editing);
      setEditing(null);
      setPreview(await api.runPreview(flow, runId, path));
      onSaved();
    } catch (e) {
      setFailed((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={`output ${present ? "" : "missing"}`}>
      <div className="output-head">
        <span className="output-name">{name}</span>
        <span className="output-shape">
          {!present ? (pending ? "not written yet" : "not produced") : preview ? shapeOf(preview) : "…"}
        </span>
        {mayBeEmpty && <Badge variant="chip">may be empty</Badge>}
        <span className="story-spacer" />
        {present && editable && editing === null && (
          <Button size="sm" onClick={() => void edit()}>
            Edit
          </Button>
        )}
      </div>

      {failed && <div className="phase-note bad">{failed}</div>}

      {editing !== null ? (
        <div className="output-edit">
          <textarea
            value={editing}
            onChange={(e) => setEditing(e.target.value)}
            aria-label={`Edit ${name}`}
          />
          <div className="output-edit-actions">
            <Button variant="primary" onClick={() => void save()} disabled={saving}>
              {saving ? "Saving…" : "Save — the next phase reads this"}
            </Button>
            <Button variant="subtle" onClick={() => setEditing(null)} disabled={saving}>
              Cancel
            </Button>
          </div>
        </div>
      ) : preview?.kind === "table" ? (
        <div className="output-table-wrap">
          <table className="output-table">
            <thead>
              <tr>
                {preview.columns.map((column, i) => (
                  <th key={i}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.rows.slice(0, open ? preview.rows.length : 6).map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {preview.rows.length > 6 && (
            <Button size="sm" variant="link" onClick={() => setOpen(!open)}>
              {open ? "Show fewer rows" : `Show the first ${preview.rows.length} rows`}
            </Button>
          )}
          {preview.rows.length === 0 && (
            <p className="muted output-empty">
              No rows{mayBeEmpty ? " — allowed for this file." : "."}
            </p>
          )}
        </div>
      ) : preview?.kind === "text" ? (
        <div className={`output-text ${open ? "open" : ""}`}>
          {name.endsWith(".md") ? (
            <Markdown remarkPlugins={[remarkGfm]}>{preview.text}</Markdown>
          ) : (
            <pre>{preview.text}</pre>
          )}
          {!open && <button className="output-more" onClick={() => setOpen(true)}>Read all</button>}
        </div>
      ) : null}
    </div>
  );
}

// --- what a phase did, in words ------------------------------------------

type StepData =
  | { kind: "search"; looked: string[] }
  | {
      kind: "tool";
      tool: string;
      label: string;
      ok: boolean | null;
      note: string;
      errors: string[];
      output: string;
      rows?: number;
    }
  | { kind: "error"; message: string };

function stepsOf(events: AgentEvent[]): StepData[] {
  const steps: StepData[] = [];
  // Parallel calls of one tool end in any order, so an end closes the earliest open start of the
  // same tool rather than the last step.
  const open: Extract<StepData, { kind: "tool" }>[] = [];
  for (const event of events) {
    const data = event.data ?? {};
    if (event.type === "tool_start" && data.tool === "search_model") {
      const last = steps[steps.length - 1];
      if (last?.kind === "search") last.looked.push(data.label ?? "");
      else steps.push({ kind: "search", looked: [data.label ?? ""] });
    } else if (event.type === "tool_start") {
      const step = {
        kind: "tool" as const,
        tool: String(data.tool),
        label: String(data.label ?? ""),
        ok: null,
        note: "",
        errors: [],
        output: "",
      };
      steps.push(step);
      open.push(step);
    } else if (event.type === "tool_end" && data.tool !== "search_model") {
      const at = open.findIndex((step) => step.tool === data.tool);
      if (at === -1) continue;
      const [step] = open.splice(at, 1);
      step.ok = Boolean(data.ok);
      step.note = String(data.note ?? "");
      step.errors = (data.errors as string[]) ?? [];
      step.output = String(data.output ?? "");
      step.rows = data.rows_total;
    } else if (event.type === "error") {
      steps.push({ kind: "error", message: String(data.message ?? "the phase failed") });
    }
  }
  return steps;
}

function Step({ step, prepared }: { step: StepData; prepared: boolean }) {
  if (step.kind === "search") {
    return (
      <li className="step">
        <span className="step-mark">⌕</span>
        <div className="step-body">
          <details>
            <summary>
              Looked up the data model
              {step.looked.length > 1 ? ` (${step.looked.length} searches)` : ""}
            </summary>
            <ul className="step-list">
              {step.looked.map((what, i) => (
                <li key={i}>{what}</li>
              ))}
            </ul>
          </details>
        </div>
      </li>
    );
  }
  if (step.kind === "error") {
    return (
      <li className="step bad">
        <span className="step-mark">✕</span>
        <div className="step-body">{step.message}</div>
      </li>
    );
  }
  const mark = step.ok === null ? <span className="spinner" /> : step.ok ? "✓" : "!";
  const tone = step.ok === null ? "" : step.ok ? "" : "warn";
  const improvised =
    prepared &&
    ((step.tool === "run_python" && step.label === "a script") ||
      (step.tool === "extract" && !step.label.startsWith("prepared ")));
  return (
    <li className={`step ${tone}`}>
      <span className="step-mark">{mark}</span>
      <div className="step-body">
        <div>
          {describe(step)}
          {improvised && (
            <span className="step-flag" title="This phase has prepared work; the agent wrote its own here">
              not the prepared one
            </span>
          )}
        </div>
        {step.errors.length > 0 && <div className="step-detail warn">{step.errors[0]}</div>}
        {step.tool === "run_python" && step.output && (
          <details>
            <summary>What the script printed</summary>
            <pre className="step-pre">{step.output}</pre>
          </details>
        )}
      </div>
    </li>
  );
}

function describe(step: Extract<StepData, { kind: "tool" }>): string {
  const running = step.ok === null;
  switch (step.tool) {
    case "extract": {
      const what = step.label.startsWith("prepared ")
        ? `the prepared query ${step.label.slice(9)}`
        : step.label;
      if (running) return `Pulling data: ${what}…`;
      if (!step.ok) return `Query refused: ${what}. The agent can correct it and try again.`;
      return `Pulled ${what} into ${step.note || "a file"}`;
    }
    case "run_python": {
      const what = step.label === "a script" ? "a Python script it wrote" : `the prepared script ${step.label}`;
      if (running) return `Running ${what}…`;
      return step.ok ? `Ran ${what}` : `${what[0].toUpperCase()}${what.slice(1)} failed${step.note ? ` — ${step.note}` : ""}`;
    }
    case "write_output":
      if (running) return `Writing ${step.label}…`;
      return step.ok ? `Wrote ${step.label}` : `Could not write ${step.label}`;
    case "explain_last_query":
      return "Read the SQL behind the last query";
    case "run_script":
      if (running) return `Running ${step.label}…`;
      return step.ok ? `Ran ${step.label}` : `${step.label} failed`;
    default:
      return `${step.tool}${step.label ? `: ${step.label}` : ""}`;
  }
}

// --- grouping and wording -------------------------------------------------

/**
 * Each event under the phase it belongs to. A run's log files events under the phase's name; a
 * log written before that falls back to the phase whose run window holds the event's time — and
 * an attempt overwritten by a re-run then falls outside every window, which is right: it is not
 * what the phase's outputs came from.
 */
function groupByPhase(events: AgentEvent[], phases: RunPhase[]): Map<string, AgentEvent[]> {
  const groups = new Map<string, AgentEvent[]>();
  const names = new Set(phases.map((p) => p.dir_name));
  for (const event of events) {
    let owner = event.turn_id && names.has(event.turn_id) ? event.turn_id : "";
    if (!owner) {
      const at = (event as AgentEvent & { at?: number }).at ?? 0;
      owner =
        phases.find(
          (p) => p.started_at > 0 && at >= p.started_at && (p.ended_at === 0 || at <= p.ended_at + 1),
        )?.dir_name ?? "";
    }
    if (!owner) continue;
    groups.set(owner, [...(groups.get(owner) ?? []), event]);
  }
  // A re-run appends a second attempt under the same name; only the latest is what is on disk.
  for (const phase of phases) {
    const mine = groups.get(phase.dir_name);
    if (!mine) continue;
    const begun = mine.map((e) => e.type).lastIndexOf("user");
    if (begun > 0) groups.set(phase.dir_name, mine.slice(begun));
  }
  return groups;
}

function readable(path: string): string {
  if (path === "brief.md") return "the brief";
  const [phase, , file] = path.split("/");
  return file ? `${file} (from ${phase})` : path;
}

function how(kind: string, tools: string[]): string {
  if (kind === "script") return "A fixed script, no model.";
  const can = [
    tools.includes("query") && "query the data model",
    tools.includes("python") && "run Python",
  ].filter(Boolean);
  return can.length ? `An agent that can ${can.join(" and ")}.` : "An agent that writes from what it reads.";
}

function shapeOf(preview: FilePreview): string {
  const size =
    preview.bytes < 1024
      ? `${preview.bytes} B`
      : preview.bytes < 1024 * 1024
        ? `${Math.round(preview.bytes / 1024)} kB`
        : `${(preview.bytes / 1024 / 1024).toFixed(1)} MB`;
  if (preview.kind !== "table") return size;
  const rows = preview.rows_total ?? preview.rows.length;
  return `${rows.toLocaleString()} rows × ${preview.columns.length} columns · ${size}`;
}

function took(phase: RunPhase): string {
  const end = phase.ended_at || Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - phase.started_at));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
