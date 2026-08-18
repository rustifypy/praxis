import { useState } from "react";

import * as api from "../api";

/**
 * The click that lets a script run, inside the step that is about to run it.
 *
 * Not a safety gate — the sandbox has no network, no credential and one writable directory, so
 * nothing here is what stops a bad script. It is the one moment the script is readable before it
 * runs, which is a different thing and the reason this exists. The script itself is above this,
 * rendered by the step, so a decision and the thing decided about are never two blocks apart.
 *
 * The decision is local state: the event is never stored, and `tool_end` arriving is what takes
 * this away for good.
 */

const CHOICES = [
  { decision: "yes", label: "Run", hint: "run this script" },
  { decision: "always", label: "Always in this chat", hint: "stop asking until a restart" },
  { decision: "no", label: "Decline", hint: "do not run it" },
] as const;

const DECIDED = {
  yes: "Running…",
  always: "Running — not asking again in this chat",
  no: "Declined",
};

export function Approval({ chatId }: { chatId: string }) {
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

  if (decided) return <div className="ask-done">{DECIDED[decided]}</div>;

  return (
    <div className="ask">
      <span className="ask-label">Run this script?</span>
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
      {failed && <div className="ask-failed">{failed}</div>}
    </div>
  );
}
