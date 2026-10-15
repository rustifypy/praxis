import { useState } from "react";

import * as api from "../api";
import { Button } from "./Button";

/**
 * The click that lets a script run. Not a safety gate (the sandbox holds no credential); it is
 * the one moment the script is read before it runs. The decision is local state only.
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

export function Approval({
  chatId,
  decide: sendTo,
}: {
  chatId: string;
  /** Where the answer goes (a Run's route). Defaults to this chat's approve route. */
  decide?: (choice: "yes" | "always" | "no") => Promise<unknown>;
}) {
  const [decided, setDecided] = useState<keyof typeof DECIDED | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");

  const decide = async (decision: keyof typeof DECIDED) => {
    setBusy(true);
    setFailed("");
    try {
      await (sendTo ? sendTo(decision) : api.approveScript(chatId, decision));
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
          <Button
            key={choice.decision}
            className={`ask-choice ask-${choice.decision}`}
            title={choice.hint}
            disabled={busy}
            onClick={() => void decide(choice.decision)}
          >
            {choice.label}
          </Button>
        ))}
      </div>
      {failed && <div className="ask-failed">{failed}</div>}
    </div>
  );
}
