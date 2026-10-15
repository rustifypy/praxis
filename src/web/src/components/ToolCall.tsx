import { useEffect, useRef, useState } from "react";

import * as api from "../api";
import { duration, useElapsed } from "../hooks";
import type { ToolEnd } from "../types";
import { Approval } from "./Approval";
import { Button } from "./Button";
import { Collapse } from "./Collapse";
import { Copy } from "./Copy";

/**
 * One step of the trace: how an answer was reached, not the rows (those are their own block).
 * The label is computed server-side from the query, so it never describes something not run.
 */
export function ToolCall({
  chatId,
  tool,
  label,
  query,
  script,
  awaiting,
  end,
  startedAt,
  ms,
  live,
  approve,
}: {
  chatId: string;
  /** Where an approval's answer goes, when not this chat's route (a Flow's Run). */
  approve?: (choice: "yes" | "always" | "no") => Promise<unknown>;
  tool: string;
  label: string;
  query?: unknown;
  script?: string;
  awaiting?: boolean;
  end?: ToolEnd;
  startedAt?: number;
  ms?: number;
  live?: string;
}) {
  const [open, setOpen] = useState(false);
  const [settling, setSettling] = useState(false);
  const wasRunning = useRef(false);
  const stream = useRef<HTMLPreElement>(null);

  const running = !end;
  const failed = !!end && !end.ok;
  const elapsed = useElapsed(startedAt, running);
  const asking = Boolean(awaiting) && running;

  useEffect(() => {
    if (wasRunning.current && !running) {
      setSettling(true);
      const timer = window.setTimeout(() => setSettling(false), 700);
      return () => window.clearTimeout(timer);
    }
    wasRunning.current = running;
  }, [running]);

  // A question folded shut is a turn that has stalled for no visible reason.
  useEffect(() => {
    if (asking) setOpen(true);
  }, [asking]);

  let summary: string;
  if (asking) summary = "waiting for you";
  else if (running) summary = duration(elapsed);
  // Only a gate rejection has no note, and only that one is retried.
  else if (failed) summary = end?.note || "rejected · retrying";
  else if (tool === "run_query")
    summary = `${end?.rows_total ?? 0} rows · ${duration(end?.duration_ms ?? ms)}`;
  else if (tool === "search_model") summary = `${end?.hits ?? 0} matches`;
  else if (end?.note) summary = end.note;
  else summary = duration(ms);

  useEffect(() => {
    const pane = stream.current;
    if (pane) pane.scrollTop = pane.scrollHeight;
  }, [live]);

  const hasScript = tool === "run_python" && label.endsWith(".py");
  const hasDetail = Boolean(
    hasScript || query || end?.sql || end?.output || end?.errors?.length || end?.warnings?.length,
  );

  return (
    <div
      className={`tool ${running ? "running" : failed ? "failed" : "ok"} ${
        settling ? "settling" : ""
      }`}
    >
      <button
        className="tool-head"
        onClick={() => hasDetail && setOpen(!open)}
        disabled={!hasDetail}
      >
        <span className="tool-mark">
          {running ? <span className="spinner" /> : failed ? "✕" : "✓"}
        </span>
        <span className="tool-label">{label || tool}</span>
        <span className="tool-summary">{summary}</span>
        {hasDetail && <span className="caret">{open ? "▾" : "▸"}</span>}
      </button>

      {running && live ? (
        <pre className="tool-stream" ref={stream}>
          {live}
        </pre>
      ) : null}

      <Collapse open={open}>
        <div className="tool-body">
          {end?.errors?.length ? (
            <div className="errors">
              {end.errors.map((e, i) => (
                <div key={i}>{e}</div>
              ))}
            </div>
          ) : null}

          {end?.warnings?.length ? (
            <div className="warnings">
              {end.warnings.map((w, i) => (
                <div key={i}>{w}</div>
              ))}
            </div>
          ) : null}

          {hasScript && (
            <Script chatId={chatId} path={label} given={script} wanted={open} asking={asking} />
          )}
          {asking && <Approval chatId={chatId} decide={approve} />}

          {query ? <Detail title="Cube query" body={JSON.stringify(query, null, 2)} /> : null}
          {end?.sql ? <Detail title="SQL" body={end.sql} mono="sql" /> : null}
          {end?.output ? <Detail title="Output" body={end.output} /> : null}
        </div>
      </Collapse>
    </div>
  );
}

const PEEK_LINES = 8;

/**
 * Live, the approval event carries the script; replayed, it is read back from disk. `wanted`
 * keeps that fetch off folded steps, since `Collapse` mounts its children either way.
 */
function Script({
  chatId,
  path,
  given,
  wanted,
  asking,
}: {
  chatId: string;
  path: string;
  given?: string;
  wanted: boolean;
  asking: boolean;
}) {
  const [fetched, setFetched] = useState("");
  // Whole while awaiting approval, a peek once answered.
  const [all, setAll] = useState(asking);
  useEffect(() => setAll(asking), [asking]);

  useEffect(() => {
    if (given || !wanted || fetched) return;
    let live = true;
    api.filePreview(chatId, path).then(
      (read) => live && setFetched(read.text),
      () => {},
    );
    return () => {
      live = false;
    };
  }, [given, wanted, fetched, chatId, path]);

  const text = (given || fetched).trimEnd();
  if (!text) return null;

  const lines = text.split("\n");
  const hidden = lines.length - PEEK_LINES;

  return (
    <div className="script">
      <div className="script-head">
        <span className="script-name">{path}</span>
        <Copy value={text} />
      </div>
      <pre className={`script-body ${!all && hidden > 0 ? "peek" : ""}`}>
        {all || hidden <= 0 ? text : lines.slice(0, PEEK_LINES).join("\n")}
      </pre>
      {hidden > 0 && (
        <Button className="script-more" onClick={() => setAll(!all)}>
          {all ? "Show less" : `Show all ${lines.length} lines`}
        </Button>
      )}
    </div>
  );
}

function Detail({ title, body, mono }: { title: string; body: string; mono?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="panel">
      <div className="panel-head">
        <button className="panel-toggle" onClick={() => setOpen(!open)}>
          <span className="caret">{open ? "▾" : "▸"}</span> {title}
        </button>
        <Copy value={body} />
      </div>
      <Collapse open={open}>
        <pre className={mono}>{body}</pre>
      </Collapse>
    </div>
  );
}
