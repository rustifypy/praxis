import type { ReactNode } from "react";

/**
 * A count or a label attached to something else.
 *
 * Nine of these existed under seven names, and the names belonged to whichever feature happened
 * to need one first — which is how `Changes` and `Problems` both came to render `.tree-count`, a
 * class owned by the file tree, for a number that has nothing to do with files.
 *
 * Three shapes, because there really are three: a `count` is bare tabular text beside a heading,
 * a `chip` is a filled label sitting inside a row, and a `pill` is a bordered tag that floats at
 * the end of one. `tone` is semantic and separate from shape — a pill and a chip can both be
 * saying the same thing went wrong.
 */
export function Badge({
  variant = "count",
  tone = "default",
  mono = false,
  title,
  children,
}: {
  variant?: "count" | "chip" | "pill";
  tone?: "default" | "accent" | "ok" | "bad" | "warn";
  /** For anything that names a member, a type or a path. */
  mono?: boolean;
  title?: string;
  children: ReactNode;
}) {
  const classes = ["badge", `badge-${variant}`];
  if (tone !== "default") classes.push(`badge-${tone}`);
  if (mono) classes.push("badge-mono");
  return (
    <span className={classes.join(" ")} title={title}>
      {children}
    </span>
  );
}
