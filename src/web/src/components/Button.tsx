import type { ButtonHTMLAttributes } from "react";

/**
 * Every control that does something, in one place.
 *
 * **There were thirty-six of these and no two agreed.** The same small secondary action was
 * `padding: 5px 11px` in the source detail, `3px 9px` in a job header, `2px 9px` on a proposal
 * and `1px 6px` in the changes list — six sizes of one button, because a control was styled by
 * whichever container it happened to sit in and there was nothing else to reach for. That is
 * also what made a disabled button invisible: twenty of those rules never wrote a `:disabled`
 * state, so `disabled={busy}` styled nothing.
 *
 * The variants are the ones the markup had already started inventing for itself — `primary`,
 * `link`, `danger` were all in use as bare class names before this existed. `icon` is a *shape*
 * and not a variant: a square control with a glyph and no label, which any of the four can be.
 *
 * Styling lives in `.btn` in `styles.css`; this exists so the variant set is a type rather than
 * a spelling, and so a container can never restyle a control it merely contains.
 */
export function Button({
  variant = "default",
  size = "md",
  icon = false,
  className = "",
  ...rest
}: {
  /** `primary` fills with the accent; `subtle` drops the border; `link` is inline underlined text. */
  variant?: "primary" | "default" | "subtle" | "danger" | "link";
  size?: "sm" | "md";
  /** Square, glyph, no label. */
  icon?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const classes = ["btn", `btn-${variant}`];
  if (size === "sm") classes.push("btn-sm");
  if (icon) classes.push("btn-icon");
  if (className) classes.push(className);
  return <button className={classes.join(" ")} {...rest} />;
}
