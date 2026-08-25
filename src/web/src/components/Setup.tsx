import { useEffect, useState } from "react";

import * as api from "../sources";
import type { JobState, Reached, Service, SourceRow } from "../sources";
import { ConnectionForm, warehouseName } from "./AddSource";
import { BuildModel } from "./BuildModel";

/**
 * First-run setup: credentials, one deployment at a time, then the model.
 *
 * **It owns the whole screen** rather than sitting in the rail. Until it finishes there is no
 * model, so chat cannot answer and a board cannot refresh — a rail offering three dead modes
 * beside the one live one is an invitation to click them.
 *
 * **Entered whenever setup is unfinished, not once.** `source.onboarded` is computed server-side
 * from the row *and* the store, so neither storing credentials nor pasting an address ends
 * first-run: someone who downloads the file, goes to run it, and comes back an hour later arrives
 * at the step they left, not at a page of panels with no sequence.
 *
 * **Setup ends with a built model, which is why there are four steps.** The pipeline used to live
 * on the data page as a stepper you had to find; a portal whose chat cannot answer until you
 * discover an admin screen is not set up. So the last step builds it, and `onboarded` is not true
 * until the store holds cubes.
 *
 * **One service at a time**, deliberately. Handing over both files and then taking both
 * addresses makes a failure ambiguous about which deployment is wrong — and the semantic layer
 * is what everything else depends on, so it is worth proving first.
 *
 * The steps are numbered from the row and never from local state: a reload lands in the same
 * place, because the row is what "where am I" is read from.
 */
export function Setup({
  source,
  job,
  onJob,
  onBuilding,
  onChanged,
}: {
  source: SourceRow | null;
  /** The running pipeline job, followed by the page that owns the event stream. */
  job: JobState | null;
  onJob: (job: JobState) => void;
  /** Whether the model is still being built. `onboarded` goes true the moment cubes land, which
   *  is mid-sequence — so the page keeps this screen up until the whole thing has finished. */
  onBuilding: (building: boolean) => void;
  onChanged: () => void;
}) {
  // Which step is live is a fact about the row and the store, not a cursor: credentials stored →
  // semantic → sandbox → model. Nothing here can be "on step 4" while the row says otherwise.
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

        {/* Required, and this is the screen where that is felt. Nothing but `run_python` needs
            it — but a portal that lets you skip it here is one where it never gets set up. */}
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

        {/* The last step, and the only one that touches the database itself: the three above
            store credentials and check addresses, and none of them has opened a connection. */}
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

/** One numbered step. Everything ahead of the current one is visibly inert rather than absent,
 *  so the shape of what is left is legible from the first screen. */
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

/** Step 1 renders the connection form's own fields — one definition of what a valid connection
 *  is, rather than a wizard-shaped copy of the same form. No Cancel: there is nowhere to go
 *  back to, which is the one thing the wizard's copy differs in. */
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

/**
 * Download, run, paste, check — for one service.
 *
 * The address is checked *before* it is stored, which is why this holds its own input rather
 * than saving on every keystroke: a URL that turns out to be wrong should not have to be undone,
 * and a stored-then-failed address is what makes a half-configured deployment look configured.
 */
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
      // One call: it checks the address and stores it only if it answered. The warnings do not
      // gate it — a contract mismatch is worth saying and not worth refusing over.
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

      <button
        className="primary"
        disabled={checking || !url.trim()}
        onClick={() => void check()}
      >
        {checking ? "Checking…" : stored ? "Check again" : "Check and continue"}
      </button>

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
