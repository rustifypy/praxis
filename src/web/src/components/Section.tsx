import type { ReactNode } from "react";

import { Badge } from "./Badge";
import { Info } from "./Info";

/**
 * One collapsible group in a list column. Only the heading is the button: the info popover is a
 * button too, and buttons can't nest. `kind` is a class suffix that colours the heading.
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
        <span className="group-count">
          <Badge>{count}</Badge>
        </span>
        {open && <span className="group-sub">{subtitle}</span>}
      </header>
      {open && (count === 0 ? empty : children)}
    </section>
  );
}
