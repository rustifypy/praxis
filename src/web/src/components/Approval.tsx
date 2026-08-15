import { useState } from "react";

import * as api from "../api";
import { Copy } from "./Copy";

/**
 * The script the agent wants to run, and the click that lets it.
 *
 * Not a safety gate — the sandbox has no network, no credential and one writable directory, so
 * nothing here is what stops a bad script. It is the one moment the script is readable before it
 * runs, which is a different thing and the reason this exists.
 *
 * The decision is local state: the event is never stored, nothing further updates this block, and
 * threading a setter up to the transcript would buy nothing.
 */

const CHOICES = [
  { decision: "yes", label: "Run", hint: "run this script" },
  { decision: "always", label: "Always in this chat", hint: "stop asking until a restart" },
  { decision: "no", label: "Decline", hint: "do not run it" },
] as const;

const DECIDED = { yes: "Ran", always: "Ran — not asking again in this chat", no: "Declined" };

export function Approval({
  chatId,
  label,
  script,
}: {
  chatId: string;
  label: string;
  script: string;
}) {
  const [decided, setDecided] = useState<keyof typeof DECIDED | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");

  const decide = async (decision: keyof typeof DECIDED) => {
    setBusy(true);
    setFailed("");
    try {
      await api.approveScript(chatId, decision);
      setDecided(decision);
    } catch (error) {
      setFailed(error instanceof Error ? error.message : "could not send that");
      setBusy(false);
    }
  };

  return (
    <div className={`ask ${decided ? "ask-done" : ""}`}>
      <div className="ask-head">
        <span className="ask-label">
          {decided ? DECIDED[decided] : "Run this script?"} · {label}
        </span>
        <Copy value={script} />
      </div>

      <pre className="ask-script">{script}</pre>

      {decided ? null : (
        <div className="ask-choices">
          {CHOICES.map((choice) => (
            <button
              key={choice.decision}
              className={`ask-choice ask-${choice.decision}`}
              title={choice.hint}
              disabled={busy}
              onClick={() => void decide(choice.decision)}
            >
              {choice.label}
            </button>
          ))}
        </div>
      )}

      {failed && <div className="ask-failed">{failed}</div>}
    </div>
  );
}
