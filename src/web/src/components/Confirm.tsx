import type { ReactNode } from "react";
import { useState } from "react";

import { useDismiss } from "../hooks";
import { Button } from "./Button";

/**
 * Destructive confirmation that costs a moment's attention: type the word.
 *
 * `window.confirm` is rejected everywhere else in this app — it freezes the page, cannot
 * be styled, and reads as a browser error — and it was least defensible here, on the one
 * action that destroys a container and a model directory that takes an LLM call per table
 * to rebuild. A muscle-memory Enter dismisses a confirm; it does not type a word.
 */
export function Confirm({
  word,
  title,
  children,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  /** What the user has to type. */
  word: string;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [typed, setTyped] = useState("");
  const ref = useDismiss<HTMLDivElement>(true, onCancel);
  const armed = typed.trim() === word;

  return (
    <div className="modal-scrim">
      <div className="modal" ref={ref} role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}</h2>
        <div className="modal-body">{children}</div>

        <label>
          Type <code>{word}</code> to confirm
          <input
            value={typed}
            autoFocus
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => armed && e.key === "Enter" && onConfirm()}
          />
        </label>

        <footer>
          <Button onClick={onCancel}>Cancel</Button>
          <Button variant="danger" disabled={!armed} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </footer>
      </div>
    </div>
  );
}
