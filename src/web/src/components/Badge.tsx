import type { ReactNode } from "react";

/**
 * A count or label attached to something. `count` is bare text beside a heading, `chip` a filled
 * label in a row, `pill` a bordered tag at its end; `tone` is independent of shape.
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
