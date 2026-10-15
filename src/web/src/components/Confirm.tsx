import type { ReactNode } from "react";
import { useState } from "react";

import { useDismiss } from "../hooks";
import { Button } from "./Button";

/** Destructive confirmation: type the word, which a muscle-memory Enter can't do. */
export function Confirm({
  word,
  title,
  children,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
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
