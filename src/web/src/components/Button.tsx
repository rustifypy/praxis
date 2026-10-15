import type { ButtonHTMLAttributes } from "react";

/** Every button, styled by `.btn` in `styles.css` so a container never restyles its controls. */
export function Button({
  variant = "default",
  size = "md",
  icon = false,
  className = "",
  ...rest
}: {
  /** `primary` fills with accent; `subtle` drops the border; `link` is inline underlined text. */
  variant?: "primary" | "default" | "subtle" | "danger" | "link";
  size?: "sm" | "md";
  /** A shape, not a variant: square, glyph, no label. */
  icon?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const classes = ["btn", `btn-${variant}`];
  if (size === "sm") classes.push("btn-sm");
  if (icon) classes.push("btn-icon");
  if (className) classes.push(className);
  return <button className={classes.join(" ")} {...rest} />;
}
