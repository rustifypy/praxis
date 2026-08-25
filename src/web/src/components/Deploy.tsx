import { useEffect, useState } from "react";

import * as api from "../sources";
import type { Service, SourceRow } from "../sources";

/**
 * The hand-over: the compose file each deployment is run with, and the box its address comes
 * back in.
 *
 * This portal does not run anything. It holds the credentials, mints the secrets, and renders
 * them into a compose file — so this panel is the entire interface between "a database is
 * configured" and "a deployment is answering", and it has to be readable enough to follow on a
 * machine that is not this one.
 *
 * The file is **downloaded and never shown**. It carries the warehouse password and all four
 * secrets, the only thing to do with it is run it, and putting it on screen would render those
 * values into the DOM for nothing. Editing it afterwards is the owner's business.
 */
export function Deploy({
  source,
  onSaved,
}: {
  source: SourceRow;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(!source.deployed);
  const [storeUrl, setStoreUrl] = useState(source.store_url);
  const [sandboxUrl, setSandboxUrl] = useState(source.sandbox_url);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Following the row rather than mirroring it once: a URL edited from the connection form has
  // to show up here too, and these inputs are the only place it is displayed.
  useEffect(() => {
    setStoreUrl(source.store_url);
    setSandboxUrl(source.sandbox_url);
  }, [source.store_url, source.sandbox_url]);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      // Checked before it is stored, so an address that does not answer is reported rather
      // than saved — the same call the wizard makes.
      const body = await api.deployed({
        store_url: storeUrl.trim(),
        sandbox_url: sandboxUrl.trim(),
      });
      const failed = Object.entries(body.checked)
        .filter(([, result]) => !result.ok)
        .map(([service, result]) => `${service}: ${result.detail || result.status}`);
      if (failed.length) setError(failed.join("; "));
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
          {open ? "Hide" : source.deployed ? "Show the files" : "Show"}
        </button>
      </div>

      {!source.deployed && (
        <p className="deploy-lead">
          The credentials are stored and the secrets are minted. Nothing is
          running yet — run each deployment wherever you want it, then paste its
          address below.
        </p>
      )}

      {open && (
        <>
          <ol className="deploy-steps">
            <li>
              <Download
                service="semantic"
                title="The semantic layer"
                blurb="Cube, the model store that holds the data model, and Cube Store. Everything the agent answers comes through it."
              />
            </li>
            <li>
              {/* Its own deployment, not a fourth service beside the others: it shares no
                  network, volume or secret with them. */}
              <Download
                service="sandbox"
                title="The sandbox"
                blurb="Where the agent runs Python — ratios, period deltas and charts drawn from a script. Separate on purpose, so it can live anywhere."
              />
            </li>
            <li>
              <p>
                In whichever directory you saved each file, and nothing else
                needs to be there:
              </p>
              <pre className="deploy-cmd">docker compose up -d</pre>
            </li>
          </ol>

          <div className="deploy-where">
            <h4>Where they ended up</h4>
            <p className="stage-hint">
              Both addresses as <em>this machine</em> reaches them — so a
              deployment on a server is its hostname, not localhost. Editing
              these later is how you follow one that moved.
            </p>
            <label>
              <span>Semantic layer</span>
              <input
                value={storeUrl}
                placeholder="http://localhost:8000"
                onChange={(e) => setStoreUrl(e.target.value)}
                spellCheck={false}
              />
            </label>
            <label>
              <span>Sandbox</span>
              <input
                value={sandboxUrl}
                placeholder="http://localhost:8600"
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

/**
 * One service's file.
 *
 * A plain link and not a fetch: the route answers with `Content-Disposition: attachment`, so the
 * browser saves it without the page ever holding the credentials it contains.
 */
function Download({
  service,
  title,
  blurb,
}: {
  service: Service;
  title: string;
  blurb: string;
}) {
  return (
    <div className="deploy-get">
      <div>
        <strong>{title}</strong>
        <p className="stage-hint">{blurb}</p>
      </div>
      <a className="deploy-download" href={api.composeUrl(service)} download>
        Download docker-compose.yml
      </a>
      <p className="stage-hint">
        It contains the warehouse password and this deployment's secrets. Treat
        it the way you would any credentials file.
      </p>
    </div>
  );
}
