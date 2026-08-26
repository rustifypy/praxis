import type { ReactNode } from "react";

/**
 * A surface with nothing on it yet.
 *
 * **Six of these were written independently and five looked different** — `--faint` at 11px in
 * the file tree, `--muted` at 13px in the served model, `--faint` at 12.5px in a dashboard tile.
 * A blank screen is the first thing anyone sees on a surface they have not used, so six of them
 * disagreeing is most of what made the product feel unfinished, and it is the one case where the
 * inconsistency was *only* ever cosmetic — nothing here was ever a bug.
 *
 * `.dash-empty` was the one that got it right — a heading, a sentence, and room around them —
 * so that is the shape all six take now. `action` is the slot it was missing: an empty surface
 * that can tell you what to do next is worth more than one that only reports being empty.
 *
 * `tight` is for the places with no room for that: a tile that must keep its grid shape, and a
 * list row standing in for a file.
 */
export function Empty({
  title,
  action,
  tight = false,
  children,
}: {
  title?: string;
  action?: ReactNode;
  tight?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={tight ? "empty empty-tight" : "empty"}>
      {title && <h3>{title}</h3>}
      <p>{children}</p>
      {action}
    </div>
  );
}
