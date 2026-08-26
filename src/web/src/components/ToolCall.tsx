import { useEffect, useRef, useState } from "react";

import * as api from "../api";
import { duration, useElapsed } from "../hooks";
import type { ToolEnd } from "../types";
import { Approval } from "./Approval";
import { Button } from "./Button";
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
 *
 * A script is the exception, and belongs here rather than beside: it *is* how the step was
 * reached. It sat in its own card under the run while approvals were a separate block, which
 * put a script's output above the script that printed it and left a dead card behind once the
 * question was answered.
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
}: {
  chatId: string;
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

  // A question folded shut is a turn that has stalled for no visible reason.
  useEffect(() => {
    if (asking) setOpen(true);
  }, [asking]);

  let summary: string;
  if (asking) summary = "waiting for you";
  else if (running) summary = duration(elapsed);
  // A note is the server saying what went wrong in its own words — "declined by the user",
  // "stopped by the user". Only a gate rejection has none, and only that one is retried.
  else if (failed) summary = end?.note || "rejected · retrying";
  else if (tool === "run_query")
    summary = `${end?.rows_total ?? 0} rows · ${duration(end?.duration_ms ?? ms)}`;
  else if (tool === "search_model") summary = `${end?.hits ?? 0} matches`;
  // A dashboard tool's whole event is its note, so that is the summary. Everything else
  // falls back to how long it took, which is all there is to say about it.
  else if (end?.note) summary = end.note;
  else summary = duration(ms);

  // Follow a running script's output, which is the only reason to show it live.
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

          {/* First, and above Output: a script is what the step did, and everything below it
              is a consequence of it. */}
          {hasScript && (
            <Script chatId={chatId} path={label} given={script} wanted={open} asking={asking} />
          )}
          {asking && <Approval chatId={chatId} />}

          {query ? <Detail title="Cube query" body={JSON.stringify(query, null, 2)} /> : null}
          {end?.sql ? <Detail title="SQL" body={end.sql} mono="sql" /> : null}
          {end?.output ? <Detail title="Output" body={end.output} /> : null}
        </div>
      </Collapse>
    </div>
  );
}

/** How much of a script is shown before it has to be asked for. Enough for the imports and the
 *  first thing it does, which is what says whether it is reading the file you meant. */
const PEEK_LINES = 8;

/**
 * The script a run ran, open to a peek.
 *
 * Two sources, one block. Live under `approve_scripts` the approval event carries it, which is
 * what makes it readable *before* the run rather than after. Replayed — or with approvals off,
 * where no approval event is ever emitted — it is only on disk, and `label` is the path
 * `save_script` wrote it to, so `/preview` reads it back. `wanted` is what keeps that read off
 * a folded step: `Collapse` mounts its children either way, so a chat with twenty runs would
 * otherwise open with twenty fetches for scripts nobody is looking at.
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
  // Whole while the question is open, a peek once it is answered: deciding needs all of it,
  // and a finished step needs enough to recognise which script it was.
  const [all, setAll] = useState(asking);
  useEffect(() => setAll(asking), [asking]);

  useEffect(() => {
    if (given || !wanted || fetched) return;
    let live = true;
    api.filePreview(chatId, path).then(
      (read) => live && setFetched(read.text),
      // A script is worth showing and not worth an error: the run's outcome is below either way.
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
