import { useEffect, useRef, useState } from "react";

/** Milliseconds since `since`, ticking while `active`. Drives the live timers. */
export function useElapsed(since: number | undefined, active: boolean): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active || !since) return;
    // 10/s is enough to read as continuous without repainting the transcript flat out.
    const timer = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [active, since]);

  return since ? Math.max(0, now - since) : 0;
}

export function duration(ms: number | undefined): string {
  if (ms === undefined) return "";
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/**
 * The URL hash, as state.
 *
 * The single source of truth for which view is showing. A mirrored React copy could
 * disagree with the address bar after a back button or a hand-edited URL.
 */
export function useHash(): string {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return hash;
}

/** Sidebar timestamps. Server sends epoch seconds. */
export function ago(epochSeconds: number): string {
  const secs = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/**
 * Close a popover on an outside click or Escape, and return the ref to wrap it in.
 *
 * `mousedown` rather than `click`: a selection drag that starts inside the panel and
 * releases outside is one click event on the document, and closing the panel underneath
 * the cursor mid-drag is maddening.
 *
 * `onClose` is held in a ref so a caller passing an inline arrow does not resubscribe
 * both listeners on every render.
 */
export function useDismiss<T extends HTMLElement>(open: boolean, onClose: () => void) {
  const ref = useRef<T>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) close.current();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return ref;
}

/**
 * Which day-bucket a timestamp falls in, for grouping the chat list.
 *
 * Compared as calendar days rather than elapsed hours: something from 11pm yesterday is
 * "Yesterday" at 1am, not "Today".
 */
export function dayGroup(epochSeconds: number): string {
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((midnight(new Date()) - midnight(new Date(epochSeconds * 1000))) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return "Older";
}
