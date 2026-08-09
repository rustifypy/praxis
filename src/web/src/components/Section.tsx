import type { ReactNode } from "react";

import { Info } from "./Info";

/**
 * One collapsible group in a master–detail list column.
 *
 * Shared by the model and grounding halves of the Serving panel because both are lists of
 * things that fall into kinds, and the header is the only place the difference between the
 * kinds is explained. Two copies would be two places to say what a view is, or what
 * `always` costs.
 *
 * **Collapsed by default.** Five sections fully expanded is a column of eighty rows, and
 * getting from Views to Certified queries meant scrolling past every cube in the model.
 * Collapsed, the whole shape of a source fits on one screen with its counts, and opening
 * one is a click. The count is on the header precisely so a shut section still says how
 * much is inside it.
 *
 * The heading is the button, not the whole header — the info popover is a button too, and
 * nesting one inside the other is invalid and swallows its clicks.
 *
 * `kind` is a class suffix, which is what colours the heading — see `.served-section.*`.
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
        <span className="served-section-count">{count}</span>
        {/* Only when open. On a shut section it is a second line of explanation for
            something you cannot see, on every one of five headers. */}
        {open && <span className="served-section-sub">{subtitle}</span>}
      </header>
      {open && (count === 0 ? empty : children)}
    </section>
  );
}
