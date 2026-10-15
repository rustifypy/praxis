import type { ReactNode } from "react";

/** Height transition via a 0fr→1fr grid row, so no measuring and no clipping max-height. */
export function Collapse({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div className={`collapse ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="collapse-inner">{children}</div>
    </div>
  );
}
