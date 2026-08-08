import type { ReactNode } from "react";
import { useState } from "react";

import { useDismiss } from "../hooks";

/**
 * An explanation, one click away instead of in the reading path.
 *
 * Everything this project knows about a screen used to be set at body weight directly
 * under the heading, which put three paragraphs between the user and the controls. The
 * words are worth keeping; the position was not.
 */
export function Info({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLSpanElement>(open, () => setOpen(false));

  return (
    <span className="info" ref={ref}>
      <button
        className="info-button"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen(!open)}
      >
        ⓘ
      </button>
      {open && <span className="info-panel">{children}</span>}
    </span>
  );
}
