import { useState } from "react";

import { useDismiss } from "../hooks";
import { Button } from "./Button";

interface MenuItem {
  label: string;
  onSelect: () => void;
  /** The reason it is unavailable, shown greyed rather than hiding the item. */
  disabled?: string;
  danger?: boolean;
}

export function Menu({ items, label = "More actions" }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss<HTMLDivElement>(open, () => setOpen(false));

  return (
    <div className="menu" ref={ref}>
      <Button
        className="menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen(!open)}
      >
        ⋯
      </Button>

      {open && (
        <div className="menu-panel" role="menu">
          {items.map((item) => (
            <Button
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
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
