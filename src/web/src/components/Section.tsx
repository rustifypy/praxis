import type { ReactNode } from "react";

import { Badge } from "./Badge";
import { Info } from "./Info";

/**
 * One collapsible group in a master–detail list column of the Serving panel. The header is the
 * only place the difference between the kinds is explained, and two copies would be two places
 * to say what a view is.
 *
 * The heading is the button, not the whole header — the info popover is a button too, and
 * nesting one inside the other is invalid and swallows its clicks. `kind` is a class
 * suffix; it colours the heading, see `.group.*`. Open state is the caller's —
 * see `ServedModel.isOpen`.
 *
 * The classes are `group-*` and not `served-*`: this is shared, and naming it for the one
 * screen it was extracted from is what made the next screen that wanted a collapsible group
 * start over instead. Five of them had.
 */
export function Section({
  kind,
  title,
  subtitle,
  count,
  info,
  empty,
  open,
  onToggle,
  children,
}: {
  kind: string;
  title: string;
  subtitle: string;
  count: number;
  info: string;
  empty: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`group ${kind}${open ? " open" : ""}`}>
      <header className="group-head">
        <button
          className="group-toggle"
          aria-expanded={open}
          onClick={onToggle}
          title={open ? `Collapse ${title.toLowerCase()}` : `Expand ${title.toLowerCase()}`}
        >
          <span className="group-caret" aria-hidden>
            ▸
          </span>
          <h4>{title}</h4>
        </button>
        <Info label={`About ${title.toLowerCase()}`}>{info}</Info>
        {/* The count stays when shut — that is what makes a collapsed column readable. */}
        <span className="group-count">
          <Badge>{count}</Badge>
        </span>
        {open && <span className="group-sub">{subtitle}</span>}
      </header>
      {open && (count === 0 ? empty : children)}
    </section>
  );
}
