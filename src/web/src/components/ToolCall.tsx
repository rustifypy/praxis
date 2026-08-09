import { useEffect, useRef, useState } from "react";

import { duration, useElapsed } from "../hooks";
import type { ToolEnd } from "../types";
import { Collapse } from "./Collapse";
import { Copy } from "./Copy";

/**
 * One step of the trace: what the agent did, in a sentence, evidence folded behind it.
 *
 * The label is computed server-side from the query itself, so it can never describe
 * something the agent did not run. A rejection is styled as a normal step rather than a
 * failure — the agent reads the reason and retries, and watching that happen is most of
 * the reason this trace exists.
 *
 * Rows are deliberately *not* here any more. This step is now about how an answer was
 * reached — the query, the SQL, and what the gate said — while the data it returned is its
 * own block below the answer. A rejected query still shows its reasons here, because it
 * produced no data to have a block of.
 */
export function ToolCall({
  tool,
  label,
  query,
  end,
  startedAt,
  ms,
}: {
  tool: string;
  label: string;
  query?: unknown;
  end?: ToolEnd;
  startedAt?: number;
  ms?: number;
}) {
  const [open, setOpen] = useState(false);
  const [settling, setSettling] = useState(false);
  const wasRunning = useRef(false);

  const running = !end;
  const failed = !!end && !end.ok;
  const elapsed = useElapsed(startedAt, running);

  // A one-shot flash on completion, so a step that finishes off-screen still reads as
  // having changed when you scroll back to it.
  useEffect(() => {
    if (wasRunning.current && !running) {
      setSettling(true);
      const timer = window.setTimeout(() => setSettling(false), 700);
      return () => window.clearTimeout(timer);
    }
    wasRunning.current = running;
  }, [running]);

  let summary: string;
  if (running) summary = duration(elapsed);
  else if (failed) summary = "rejected · retrying";
  else if (tool === "run_query")
    summary = `${end?.rows_total ?? 0} rows · ${duration(end?.duration_ms ?? ms)}`;
  else if (tool === "search_model") summary = `${end?.hits ?? 0} matches`;
  else summary = duration(ms);

  const hasDetail = Boolean(query || end?.sql || end?.errors?.length || end?.warnings?.length);

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

          {query ? (
            <Detail title="Cube query" body={JSON.stringify(query, null, 2)} />
          ) : null}
          {end?.sql ? <Detail title="SQL" body={end.sql} mono="sql" /> : null}
        </div>
      </Collapse>
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
