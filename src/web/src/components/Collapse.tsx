import type { ReactNode } from "react";

/**
 * Height transition without measuring anything.
 *
 * A grid row animated from 0fr to 1fr resolves to the child's natural height, so
 * disclosures of unknown size — SQL, row tables, reasoning — open smoothly with no
 * ResizeObserver and no hardcoded max-height that clips long content.
 */
export function Collapse({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div className={`collapse ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="collapse-inner">{children}</div>
    </div>
  );
}
