import type { ReactNode } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useDismiss } from "../hooks";

/**
 * An explanation, one click away instead of in the reading path.
 *
 * Everything this project knows about a screen used to be set at body weight directly
 * under the heading, which put three paragraphs between the user and the controls. The
 * words are worth keeping; the position was not.
 *
 * Positioned `fixed` and measured, not absolute. These icons sit inside the sources
 * list column (about 200px wide) and inside the chat header's popover, which scrolls —
 * and `overflow-y: auto` computes `overflow-x` to `auto` too, so an absolute panel wider
 * than its container does not simply spill, it makes that container scroll sideways. A
 * fixed layer has no clipping ancestor, and clamping to the viewport is then enough to
 * be correct everywhere it is used.
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
    // Prefer left-aligned with the icon; slide back inside when that would overflow.
    const left = Math.max(8, Math.min(anchor.left - 8, window.innerWidth - box.width - 8));
    setAt({ top: anchor.bottom + 6, left });
  }, [open]);

  // A fixed panel does not travel with its anchor, so close rather than let it detach.
  // Capture phase: the scroller is usually an inner element, not the window.
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
          // Hidden for the one frame between mounting and being measured, so it is never
          // painted at the top-left of the screen first.
          style={at ? { top: at.top, left: at.left } : { visibility: "hidden" }}
        >
          {children}
        </span>
      )}
    </span>
  );
}
