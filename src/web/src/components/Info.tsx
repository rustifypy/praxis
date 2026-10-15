import type { ReactNode } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useDismiss } from "../hooks";

/**
 * An explanation one click away. Positioned `fixed`, not absolute: inside a scroller,
 * `overflow-y: auto` forces `overflow-x: auto`, so a wide absolute panel scrolls it sideways.
 */
export function Info({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLSpanElement>(null);
  const ref = useDismiss<HTMLSpanElement>(open, () => setOpen(false));

  useLayoutEffect(() => {
    if (!open) {
      setAt(null);
      return;
    }
    const anchor = button.current?.getBoundingClientRect();
    const box = panel.current?.getBoundingClientRect();
    if (!anchor || !box) return;
    const left = Math.max(8, Math.min(anchor.left - 8, window.innerWidth - box.width - 8));
    setAt({ top: anchor.bottom + 6, left });
  }, [open]);

  // A fixed panel doesn't follow its anchor, so close on scroll (captured: inner scrollers).
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    document.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  return (
    <span className="info" ref={ref}>
      <button
        ref={button}
        className="info-button"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen(!open)}
      >
        ⓘ
      </button>
      {open && (
        <span
          className="info-panel"
          ref={panel}
          // Hidden until measured, so it never flashes at the top-left.
          style={at ? { top: at.top, left: at.left } : { visibility: "hidden" }}
        >
          {children}
        </span>
      )}
    </span>
  );
}
