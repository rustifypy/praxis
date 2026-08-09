import { useState } from "react";

import { useDismiss } from "../hooks";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  /** Shown greyed with the reason as its subtitle, rather than hidden. A control that
   *  vanishes teaches nothing; one that says why it is unavailable does. */
  disabled?: string;
  /** Destructive. Rendered in the bad tone and ruled off from the routine actions above
   *  it, so it cannot be hit by momentum on the way to Edit. */
  danger?: boolean;
}

/**
 * The overflow menu.
 *
 * Container controls are infrequent and mutually similar — restart, stop, output, edit —
 * so as seven peer buttons they crowded out the one action that actually moves the source
 * forward. Ranking them below a single primary is the whole point.
 */
export function Menu({ items, label = "More actions" }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));

  return (
    <div className="menu" ref={ref}>
      <button
        className="menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen(!open)}
      >
        ⋯
      </button>

      {open && (
        <div className="menu-panel" role="menu">
          {items.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              className={item.danger ? "danger" : undefined}
              disabled={Boolean(item.disabled)}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              <span>{item.label}</span>
              {item.disabled && <small>{item.disabled}</small>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
