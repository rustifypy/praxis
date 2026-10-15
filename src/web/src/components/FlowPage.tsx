import { Suspense, lazy, useCallback, useEffect, useState } from "react";

import * as api from "../api";
import type { FlowDetail } from "../types";

import { Badge } from "./Badge";
import { Button } from "./Button";
import { Empty } from "./Empty";
import { FlowChat } from "./FlowChat";
import { FlowFiles } from "./FlowFiles";
import { FlowRuns } from "./FlowRuns";

const CodeEditor = lazy(() => import("./CodeEditor"));

/**
 * One flow, as Edit and Runs tabs. Whether it runs comes from the server (`contract.load`) and
 * is never recomputed here.
 */
export function FlowPage({ flow }: { flow: string }) {
  const [detail, setDetail] = useState<FlowDetail | null>(null);
  const [tab, setTab] = useState<"edit" | "runs">("edit");
  const [openPath, setOpenPath] = useState("");
  const [text, setText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    try {
      setDetail(await api.flowDetail(flow));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [flow]);

  useEffect(() => {
    setOpenPath("");
    setText("");
    setSavedText("");
    void reload();
  }, [flow, reload]);

  const open = async (path: string) => {
    setOpenPath(path);
    try {
      const body = await api.flowFile(flow, path);
      setText(body);
      setSavedText(body);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const save = async () => {
    try {
      const written = await api.writeFlowFile(flow, openPath, text);
      setSavedText(text);
      // The write always lands; `ok` says whether the flow still runs.
      setError(written.ok ? "" : written.errors.join("\n"));
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!detail) {
    return (
      <div className="flow-page">
        <Empty>{error || "Loading…"}</Empty>
      </div>
    );
  }

  return (
    <div className="flow-page">
      <header className="flow-head">
        <div className="flow-title">
          <h2>{detail.title || detail.name}</h2>
          <Badge>{detail.phases.length} phases</Badge>
          {detail.ok ? (
            <span className="flow-ok">runs</span>
          ) : (
            <span className="flow-warn">will not run</span>
          )}
        </div>
        {detail.description && <p className="flow-desc">{detail.description}</p>}
        <div className="segmented flow-tabs" role="group" aria-label="What to show">
          <button className={tab === "edit" ? "on" : ""} onClick={() => setTab("edit")}>
            Edit
          </button>
          <button className={tab === "runs" ? "on" : ""} onClick={() => setTab("runs")}>
            Runs
          </button>
        </div>
      </header>

      {/* Verbatim from the server; the flow agent sees the same lines. */}
      {detail.errors.length > 0 && (
        <ul className="flow-problems">
          {detail.errors.map((line) => (
            <li key={line} className="bad">
              {line}
            </li>
          ))}
        </ul>
      )}
      {detail.warnings.length > 0 && (
        <ul className="flow-problems">
          {detail.warnings.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}

      {tab === "edit" ? (
        <div className="flow-edit">
          <FlowFiles
            files={detail.files}
            open={openPath}
            unsaved={new Set(text !== savedText && openPath ? [openPath] : [])}
            onOpen={(path) => void open(path)}
          />

          <div className="flow-editor">
            {openPath ? (
              <>
                <div className="flow-file-head">
                  <strong>{openPath}</strong>
                  <Button
                    variant="primary"
                    onClick={() => void save()}
                    disabled={text === savedText}
                  >
                    {text === savedText ? "Saved" : "Save"}
                  </Button>
                </div>
                <Suspense fallback={<p className="muted editor-blank">Loading the editor…</p>}>
                  <CodeEditor
                    path={openPath}
                    value={text}
                    original={null}
                    readOnly={false}
                    onChange={setText}
                    onReviewed={() => {}}
                  />
                </Suspense>
              </>
            ) : (
              <Empty>
                Pick a file, or ask on the right. A phase is two files: `phase.yml` for what it
                reads and writes, `CONTEXT.md` for what it does.
              </Empty>
            )}
            {error && <div className="editor-note bad">{error}</div>}
          </div>

          <FlowChat flow={flow} onChanged={() => void reload()} />
        </div>
      ) : (
        <FlowRuns flow={flow} runnable={detail.ok} />
      )}
    </div>
  );
}
