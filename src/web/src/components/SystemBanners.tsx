import type { SourceRow } from "../sources";

/** What is unavailable machine-wide (the sandbox), shown before and after connecting. */
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
