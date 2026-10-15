import { useEffect, useState } from "react";

import * as api from "../sources";
import type { JobState, Reached, Service, SourceRow } from "../sources";
import { Button } from "./Button";
import { ConnectionForm, warehouseName } from "./AddSource";
import { BuildModel } from "./BuildModel";

/**
 * First-run setup: credentials, one deployment at a time (so a failure names its deployment),
 * then the model. The step is read from the row, never local state, so a reload resumes it.
 */
export function Setup({
  source,
  job,
  onJob,
  onBuilding,
  onChanged,
}: {
  source: SourceRow | null;
  job: JobState | null;
  onJob: (job: JobState) => void;
  /** `onboarded` turns true mid-build, so this keeps the screen up until the build ends. */
  onBuilding: (building: boolean) => void;
  onChanged: () => void;
}) {
  const step = !source ? 1 : !source.store_url ? 2 : !source.sandbox_url ? 3 : 4;

  return (
    <div className="setup">
      <header className="setup-head">
        <h1>Set up your data</h1>
        <p>
          The portal runs nothing itself. You give it a connection, it gives you
          a file per deployment, and you run those wherever you like — this
          machine or a server. Then it needs the addresses back, and it builds
          your data model.
        </p>
      </header>

      <ol className="setup-steps">
        <Step n={1} now={step} title="Your database or warehouse">
          {source ? (
            <p className="setup-done">
              <strong>{source.label}</strong> · {warehouseName(source.driver)}
              {" — stored, and the deployment secrets are minted."}
            </p>
          ) : (
            <Credentials onConnected={onChanged} />
          )}
        </Step>

        <Step n={2} now={step} title="The semantic layer">
          {source && (
            <Handover
              service="semantic"
              blurb="Cube, the model store that holds your data model, and Cube Store. Everything the agent answers comes through it."
              placeholder="http://localhost:8000"
              stored={source.store_url}
              onSaved={onChanged}
            />
          )}
        </Step>

        {/* Required here, though only `run_python` needs it: skipped, it never gets set up. */}
        <Step n={3} now={step} title="The sandbox">
          {source && step >= 3 && (
            <Handover
              service="sandbox"
              blurb="Where the agent runs Python: ratios, period deltas, and charts drawn from a script. Its own deployment, sharing no network, volume or secret with the other."
              placeholder="http://localhost:8600"
              stored={source.sandbox_url}
              onSaved={onChanged}
            />
          )}
        </Step>

        <Step n={4} now={step} title="The data model">
          {source && step >= 4 && (
            <>
              <p className="stage-hint">
                One cube per table, with its keys and joins — this is what a question is answered
                from. It reads your schemas first, so choosing tables is instant.
              </p>
              <BuildModel
                job={job}
                rebuilding={false}
                onJob={onJob}
                onRunning={onBuilding}
              />
            </>
          )}
        </Step>
      </ol>
    </div>
  );
}

function Step({
  n,
  now,
  title,
  children,
}: {
  n: number;
  now: number;
  title: string;
  children?: React.ReactNode;
}) {
  const state = n < now ? "done" : n === now ? "live" : "later";
  return (
    <li className={`setup-step ${state}`}>
      <span className="setup-n">{state === "done" ? "✓" : n}</span>
      <div className="setup-body">
        <h2>{title}</h2>
        {state === "later" ? (
          <p className="stage-hint">Once the previous step is done.</p>
        ) : (
          children
        )}
      </div>
    </li>
  );
}

function Credentials({ onConnected }: { onConnected: () => void }) {
  return (
    <ConnectionForm
      onConnect={async (body) => {
        await api.connect(body);
        onConnected();
      }}
      onSave={async () => {}}
    />
  );
}

/** Download, run, paste, check. The address is stored only once it answers, so a wrong one
 *  never makes a half-configured deployment look configured. */
function Handover({
  service,
  blurb,
  placeholder,
  stored,
  onSaved,
}: {
  service: Service;
  blurb: string;
  placeholder: string;
  stored: string;
  onSaved: () => void;
}) {
  const [url, setUrl] = useState(stored);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<Reached | null>(null);
  const [error, setError] = useState("");

  useEffect(() => setUrl(stored), [stored]);

  const check = async () => {
    setChecking(true);
    setError("");
    setResult(null);
    try {
      const body = await api.deployed(
        service === "semantic" ? { store_url: url.trim() } : { sandbox_url: url.trim() },
      );
      const reached = body.checked[service] ?? null;
      setResult(reached);
      if (reached?.ok) onSaved();
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
      <p className="stage-hint">{blurb}</p>

      <div className="setup-get">
        <a className="deploy-download" href={api.composeUrl(service)} download>
          Download docker-compose.yml
        </a>
        <span className="stage-hint">
          It holds your warehouse password and this deployment's secrets. Treat
          it the way you would any credentials file — and keep it, since editing
          it later is how you change what this runs on.
        </span>
      </div>

      <p>Save it anywhere, then in that directory — nothing else needs to be there:</p>
      <pre className="deploy-cmd">docker compose up -d</pre>

      <label className="setup-url">
        <span>Its address, as this machine reaches it</span>
        <input
          value={url}
          placeholder={placeholder}
          spellCheck={false}
          onChange={(e) => {
            setUrl(e.target.value);
            setResult(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && url.trim() && !checking) void check();
          }}
        />
        <small>
          A deployment on a server is its hostname, not localhost.
        </small>
      </label>

      <Button
        variant="primary"
        disabled={checking || !url.trim()}
        onClick={() => void check()}
      >
        {checking ? "Checking…" : stored ? "Check again" : "Check and continue"}
      </Button>

      {error && <p className="banner bad">{error}</p>}
      {result && !result.ok && (
        <p className="banner bad">
          {result.detail || "It did not answer."}
        </p>
      )}
      {result?.ok && <p className="banner good">It answered. </p>}
      {result?.warnings.map((warning) => (
        <p className="banner warn" key={warning}>
          {warning}
        </p>
      ))}
    </>
  );
}
