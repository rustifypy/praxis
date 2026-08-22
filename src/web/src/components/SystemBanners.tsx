import type { SourceRow } from "../sources";

/**
 * What is unavailable machine-wide. Docker is the only one left, and it is not fatal.
 *
 * Nothing here is about the deployment: it runs wherever its owner put it, so whether it is
 * up is a health probe on the page below rather than a fact about this machine. Its own
 * component because both states of the data page show it — before a database is connected and
 * after — and a second copy is one that would drift.
 */
export function SystemBanners({ source }: { source: SourceRow | null }) {
  if (!source?.sandbox_reason) return null;
  return (
    <p className="banner warn">
      The sandbox is unavailable, so the agent cannot run Python — no ratios,
      period deltas or charts drawn from a script. Everything else works:
      connecting, building the model and asking questions all go over HTTP. (
      {source.sandbox_reason})
    </p>
  );
}
