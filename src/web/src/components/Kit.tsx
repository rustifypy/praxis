import { Root } from "@cube-dev/ui-kit";
import type { ReactNode } from "react";

/**
 * The UI kit's root, mounted only around what uses the kit (the model editor), so its ~1 MB
 * ships in that page's chunk instead of in front of chat. `Root` renders `display: contents`.
 *
 * Tokens point back at `styles.css` variables so the stylesheet stays the one place the theme
 * lives, dark half included. `$radius` restores the app's corner: the kit's own 6px lands on
 * `body` and would override it.
 */
const TOKENS = {
  "#surface": "var(--bg)",
  "#surface-2": "var(--panel)",
  "#surface-3": "var(--raised)",
  "#border": "var(--line)",
  "#surface-text": "var(--ink)",
  "#surface-text-soft": "var(--muted)",
  "#placeholder": "var(--faint)",
  // The kit's cool text greys, mapped to the app's warm ink steps.
  "#dark-02": "var(--ink)",
  "#dark-03": "var(--muted)",
  "#dark-04": "var(--faint)",
  "#dark-05": "var(--line)",
  $radius: "var(--app-radius)",
};

/** Seeds for every hue `TOKENS` does not name; the kit derives the dark variants. */
const PALETTE = {
  accent: "#3a5bd9",
  themes: { success: "#2f7d4f", danger: "#b3402f", warning: "#9a6b12" },
};

/** The kit injects its own `body` rule at runtime; this restates `styles.css`'s so it wins. */
const BODY = {
  background: "var(--bg)",
  color: "var(--ink)",
  fontFamily: "var(--sans)",
  fontSize: "var(--text-lg)",
  lineHeight: "1.6",
  letterSpacing: "normal",
};

export function Kit({ children }: { children: ReactNode }) {
  return (
    <Root
      tokens={TOKENS}
      palette={PALETTE}
      bodyStyles={BODY}
      font="var(--sans)"
      monospaceFont="var(--mono)"
    >
      {children}
    </Root>
  );
}
