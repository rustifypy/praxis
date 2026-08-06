import { useEffect, useState } from "react";

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

/** Sidebar timestamps. Server sends epoch seconds. */
export function ago(epochSeconds: number): string {
  const secs = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}
