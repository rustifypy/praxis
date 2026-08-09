import type { ReactNode } from "react";

import { Info } from "./Info";

/**
 * One collapsible group in a master–detail list column, shared by the model and grounding
 * halves of the Serving panel. The header is the only place the difference between the
 * kinds is explained, and two copies would be two places to say what a view is.
 *
 * The heading is the button, not the whole header — the info popover is a button too, and
 * nesting one inside the other is invalid and swallows its clicks. `kind` is a class
 * suffix; it colours the heading, see `.served-section.*`. Open state is the caller's —
 * see `ServedModel.isOpen`.
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
    <section className={`served-section ${kind}${open ? " open" : ""}`}>
      <header className="served-section-head">
        <button
          className="served-section-toggle"
          aria-expanded={open}
          onClick={onToggle}
          title={open ? `Collapse ${title.toLowerCase()}` : `Expand ${title.toLowerCase()}`}
        >
          <span className="served-caret" aria-hidden>
            ▸
          </span>
          <h4>{title}</h4>
        </button>
        <Info label={`About ${title.toLowerCase()}`}>{info}</Info>
        {/* The count stays when shut — that is what makes a collapsed column readable. */}
        <span className="served-section-count">{count}</span>
        {open && <span className="served-section-sub">{subtitle}</span>}
      </header>
      {open && (count === 0 ? empty : children)}
    </section>
  );
}
