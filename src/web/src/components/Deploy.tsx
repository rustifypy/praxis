import { useCallback, useEffect, useState } from "react";

import * as api from "../sources";
import type { SourceRow } from "../sources";
import { Copy } from "./Copy";

/**
 * The hand-over: the environment file to run the deployment with, and the box the address
 * comes back in.
 *
 * This portal does not run Cube. It holds the credentials, mints the secrets, and renders
 * them — so this panel is the entire interface between "a database is configured" and "a
 * deployment is answering", and it has to be readable enough that someone can follow it on a
 * machine that is not this one.
 *
 * Shown collapsed once the addresses are set, because it stops being a task and becomes a
 * reference: the file is still what a credential change has to be re-run with.
 */
export function Deploy({
  source,
  onSaved,
}: {
  source: SourceRow;
  onSaved: () => void;
}) {
  const [env, setEnv] = useState<string>("");
  const [directory, setDirectory] = useState("");
  const [sandboxEnv, setSandboxEnv] = useState("");
  const [sandboxDirectory, setSandboxDirectory] = useState("");
  const [compose, setCompose] = useState("docker-compose.yml");
  const [open, setOpen] = useState(!source.deployed);
  const [storeUrl, setStoreUrl] = useState(source.store_url);
  const [sandboxUrl, setSandboxUrl] = useState(source.sandbox_url);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    void api
      .deployEnv()
      .then((body) => {
        setEnv(body.env);
        setDirectory(body.directory);
        setSandboxEnv(body.sandbox_env);
        setSandboxDirectory(body.sandbox_directory);
        setCompose(body.compose);
      })
      .catch((err) =>
        setError(String(err instanceof Error ? err.message : err)),
      );
  }, []);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  // Following the row rather than mirroring it once: a URL edited from the connection form
  // has to show up here too, and these inputs are the only place it is displayed.
  useEffect(() => {
    setStoreUrl(source.store_url);
    setSandboxUrl(source.sandbox_url);
  }, [source.store_url, source.sandbox_url]);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await api.edit({
        store_url: storeUrl.trim(),
        sandbox_url: sandboxUrl.trim(),
      });
      onSaved();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setSaving(false);
    }
  };

  const changed =
    storeUrl.trim() !== source.store_url ||
    sandboxUrl.trim() !== source.sandbox_url;

  return (
    <section className="detail-panel">
      <div className="panel-head">
        <h3>Deployment</h3>
        <button
          className="link"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open
            ? "Hide"
            : source.deployed
              ? "Show the environment file"
              : "Show"}
        </button>
      </div>

      {!source.deployed && (
        <p className="deploy-lead">
          The credentials are stored and the secrets are minted. Nothing is
          running yet — run the deployment wherever you want it, then paste its
          address below.
        </p>
      )}

      {open && (
        <>
          <ol className="deploy-steps">
            <li>
              <p>
                Save this as <code>.env</code> in{" "}
                <code>{directory || "semantic/"}</code>, beside{" "}
                <code>{compose}</code>.
              </p>
              {/* The password is in here. It has to be — nothing can start Cube without it —
                  which is why this is the one thing the portal shows in full. */}
              <div className="deploy-file">
                <div className="deploy-file-head">
                  <span className="muted">.env</span>
                  <Copy value={env} label="Copy" />
                </div>
                <pre>{env || "Loading…"}</pre>
              </div>
              <p className="stage-hint">
                It contains the warehouse password and the deployment's secrets.
                Treat it the way you would any credentials file.
              </p>
            </li>
            <li>
              <p>Then, in that directory:</p>
              <div className="deploy-file">
                <div className="deploy-file-head">
                  <span className="muted">shell</span>
                  <Copy value="docker compose up -d" label="Copy" />
                </div>
                <pre>docker compose up -d</pre>
              </div>
              <p className="stage-hint">
                Three containers: Cube, the model store that holds the data
                model, and Cube Store. On Apple Silicon uncomment{" "}
                <code>CUBESTORE_TAG</code> first.
              </p>
            </li>
            <li>
              {/* Its own deployment, not a fourth service beside the others: it shares no
                  network, volume or secret with them, and everything works without it except
                  running Python. */}
              <p>
                Optional — the sandbox, where the agent runs Python. Separate on
                purpose, so it can live anywhere, or nowhere. Save this as{" "}
                <code>.env</code> in{" "}
                <code>{sandboxDirectory || "sandbox-service/"}</code> and bring
                it up the same way.
              </p>
              <div className="deploy-file">
                <div className="deploy-file-head">
                  <span className="muted">sandbox-service/.env</span>
                  <Copy value={sandboxEnv} label="Copy" />
                </div>
                <pre>{sandboxEnv || "Loading…"}</pre>
              </div>
              <p className="stage-hint">
                Without it there are no ratios, period deltas or charts drawn
                from a script. Everything else — connecting, building the model,
                asking questions — is unaffected.
              </p>
            </li>
          </ol>

          <div className="deploy-where">
            <h4>Where it ended up</h4>
            <p className="stage-hint">
              Both addresses as <em>this machine</em> reaches them — so a
              deployment on a server is its hostname, not localhost. Editing
              these later is how you follow one that moved.
            </p>
            <label>
              <span>Semantic layer</span>
              <input
                value={storeUrl}
                placeholder="http://localhost:8500"
                onChange={(e) => setStoreUrl(e.target.value)}
                spellCheck={false}
              />
            </label>
            {/* Optional, and the only one of the three that is: without it the agent cannot
                run Python — no ratios, period deltas or charts drawn from a script — and
                everything else works. */}
            <label>
              <span>Sandbox</span>
              <input
                value={sandboxUrl}
                placeholder="http://localhost:8600 (optional)"
                onChange={(e) => setSandboxUrl(e.target.value)}
                spellCheck={false}
              />
            </label>
            <button
              className="primary"
              disabled={saving || !changed || !storeUrl.trim()}
              onClick={() => void save()}
            >
              {saving
                ? "Saving…"
                : source.deployed
                  ? "Update the addresses"
                  : "Connect to it"}
            </button>
          </div>

          {error && <p className="banner bad">{error}</p>}
        </>
      )}
    </section>
  );
}
