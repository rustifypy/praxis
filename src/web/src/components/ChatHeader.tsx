import { useCallback, useEffect, useState } from "react";

import { useDismiss } from "../hooks";
import * as api from "../sources";
import type { ServedModel as Model, SourceRow } from "../sources";
import { healthOf, servingSummary } from "../sources";
import { ServedModel } from "./ServedModel";

/**
 * Which source this conversation queries, and what it can be asked about.
 *
 * Chat had no header at all. The answer to "what am I querying?" was the sidebar footer,
 * rendering `CUBE_URL` as a bare `127.0.0.1:4001` — a port number the user cannot map
 * back to a source without opening the sources panel and comparing. For a product whose
 * premise is governed access to one semantic layer, that is the wrong place for the one
 * fact that says which layer.
 *
 * The panel also hosts `ServedModel`, which is a chat-time component: "what can I ask
 * about" is the question you have with a cursor in the composer, and it was living at the
 * bottom of an admin page.
 */
export function ChatHeader({
  sources,
  cubeUrl,
  onShowSources,
}: {
  sources: SourceRow[];
  cubeUrl: string;
  onShowSources: (name?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState<Model | null>(null);
  const [loading, setLoading] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));

  const active = sources.find((source) => source.active) ?? null;
  const health = active ? healthOf(active) : null;

  const load = useCallback(() => {
    if (!active?.running) return;
    setLoading(true);
    void api
      .servedModel(active.name)
      .then(setModel)
      .catch((err) => setModel({ serving: false, error: String(err), entities: [] }))
      .finally(() => setLoading(false));
  }, [active?.name, active?.running]);

  // Lazily, on first open: reading /v1/meta on every page load would cost a request per
  // launch for a panel most turns never open.
  useEffect(() => {
    if (open && !model) load();
  }, [open, model, load]);

  if (!active) {
    return (
      <header className="chat-head">
        <button className="src-chip warn" onClick={() => onShowSources()}>
          <span className="health-dot warn" />
          <span className="src-chip-name">No active source</span>
          <span className="src-chip-meta">
            {cubeUrl ? `${cubeUrl.replace(/^https?:\/\//, "")} matches no source` : "CUBE_URL is unset"}
          </span>
        </button>
      </header>
    );
  }

  return (
    <header className="chat-head">
      <div className="src-chip-wrap" ref={ref}>
        <button className="src-chip" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className={`health-dot ${health?.tone}`} />
          <span className="src-chip-name">{active.name}</span>
          <span className="src-chip-meta">
            {active.serving ? servingSummary(active.serving) : health?.label}
          </span>
          <span className="src-chip-caret" aria-hidden>
            ▾
          </span>
        </button>

        {open && (
          <div className="src-panel">
            <div className="src-panel-switch">
              <h4>Querying</h4>
              {sources.map((source) => (
                <div
                  key={source.name}
                  className={`src-switch-row ${source.active ? "on" : ""}`}
                  title={
                    source.active
                      ? undefined
                      : `Set CUBE_URL=${source.url} and restart analyst to query this one`
                  }
                >
                  <span className={`health-dot ${healthOf(source).tone}`} />
                  <span className="src-switch-name">{source.name}</span>
                  <span className="src-switch-meta">
                    {source.serving ? servingSummary(source.serving) : healthOf(source).label}
                  </span>
                  {source.active ? (
                    <span className="badge accent">active</span>
                  ) : (
                    // The switch itself is not built yet — swapping mid-conversation needs
                    // a decision about turns already in flight against the old source.
                    // Until then this is the instruction, in text rather than in a tooltip
                    // on a disabled button, which several browsers never show at all.
                    <span className="src-switch-how">
                      <code>CUBE_URL={source.url}</code> + restart
                    </span>
                  )}
                </div>
              ))}
              <button className="link" onClick={() => onShowSources(active.name)}>
                Manage sources →
              </button>
            </div>

            <div className="src-panel-model">
              <h4>What you can ask about</h4>
              {active.running ? (
                <ServedModel model={model} loading={loading} onRefresh={load} />
              ) : (
                <p className="served-empty">
                  The container is stopped, so Cube cannot say what it would serve.
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
