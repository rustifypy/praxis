import { useCallback, useEffect, useState } from "react";

import * as api from "../api";
import type { McpAccess } from "../types";
import { Button } from "./Button";
import { Confirm } from "./Confirm";
import { Copy } from "./Copy";
import { Info } from "./Info";

/**
 * Agent access: the MCP endpoint and what to paste into a client. The config blocks are the
 * server's own strings (`web/mcp.client_config`), never assembled here.
 */
export function McpPage() {
  const [access, setAccess] = useState<McpAccess | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [shown, setShown] = useState(false);

  const load = useCallback(async () => {
    try {
      setAccess(await api.mcpAccess());
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Masks the token everywhere it is quoted, for display only; Copy gets the real string.
  const mask = (text: string) =>
    shown || !access ? text : text.replaceAll(access.token, "•".repeat(access.token.length));

  async function guard(work: () => Promise<void>) {
    setBusy(true);
    try {
      await work();
      setError("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="detail detail-page">
      <section className="detail-panel">
        <div className="panel-head">
          <h3>Agent access</h3>
          <Info label="About agent access">
            The same model, opened to agents that are not this one — Claude Desktop, Claude
            Code, anything speaking MCP. They get read-only tools and reach the identical
            four-tier gate a chat turn does, so a query refused here is refused there. Nothing
            can be written: no dashboards, no model edits, no Python.
            <br />
            <br />
            The token is stored rather than minted per launch, so a config pasted into a client
            keeps working across restarts. Rotating it revokes every copy at once.
          </Info>
        </div>

        {!access ? (
          <p className="muted">{error || "Loading…"}</p>
        ) : (
          <>
            <div className="mcp-row">
              <button
                className={`mcp-toggle ${access.enabled ? "on" : ""}`}
                role="switch"
                aria-checked={access.enabled}
                disabled={busy}
                onClick={() => {
                  const enabled = !access.enabled;
                  // Optimistic, then corrected by the response.
                  setAccess({ ...access, enabled });
                  void guard(async () => {
                    const out = await api.setMcpEnabled(enabled);
                    setAccess((prev) => (prev ? { ...prev, enabled: out.enabled } : prev));
                  });
                }}
              >
                <span className="mcp-track">
                  <span className="mcp-knob" />
                </span>
                <span>Serve tools over MCP</span>
              </button>
              <span className="muted">
                {access.enabled ? access.url : "the endpoint answers 404"}
              </span>
            </div>

            {error && <p className="detail-desc">{error}</p>}

            {access.enabled && (
              <>
                <p className="detail-desc">
                  {access.tools.map((t) => t.name).join(", ")} — read-only, and the portal must
                  be running for them to answer.
                </p>

                <div className="mcp-field">
                  <div className="mcp-field-head">
                    <span>Token</span>
                    <div className="mcp-actions">
                      <Button onClick={() => setShown(!shown)}>{shown ? "Hide" : "Show"}</Button>
                      <Button disabled={busy} onClick={() => setConfirmRotate(true)}>
                        Rotate
                      </Button>
                      <Copy value={access.token} />
                    </div>
                  </div>
                  <code>{mask(access.token)}</code>
                </div>

                <div className="mcp-field">
                  <div className="mcp-field-head">
                    <span>Claude Desktop — claude_desktop_config.json</span>
                    <div className="mcp-actions">
                      <Copy value={access.desktop} />
                    </div>
                  </div>
                  <pre>{mask(access.desktop)}</pre>
                </div>

                <div className="mcp-field">
                  <div className="mcp-field-head">
                    <span>Claude Code</span>
                    <div className="mcp-actions">
                      <Copy value={access.cli} />
                    </div>
                  </div>
                  <pre>{mask(access.cli)}</pre>
                </div>
              </>
            )}
          </>
        )}
      </section>

      {confirmRotate && (
        <Confirm
          word="rotate"
          title="Rotate the token?"
          confirmLabel="Rotate"
          onCancel={() => setConfirmRotate(false)}
          onConfirm={() => {
            setConfirmRotate(false);
            void guard(async () => {
              const next = await api.rotateMcpToken();
              setAccess((prev) => (prev ? { ...prev, ...next } : prev));
              setShown(true);
            });
          }}
        >
          <p>
            A new token stops every config already pasted anywhere — Claude Desktop, Claude
            Code, any other client — until each one is updated with the new value.
          </p>
        </Confirm>
      )}
    </div>
  );
}
