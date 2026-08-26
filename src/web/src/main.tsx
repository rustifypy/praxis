import { Root } from "@cube-dev/ui-kit";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";

/**
 * The kit's tokens, pointed back at this app's own.
 *
 * A tasty `#name` token compiles to `var(--name-color)` and a `$name` to `var(--name)`, and the
 * kit writes its whole generated set onto `body`. `Root` writes these onto a nearer ancestor, so
 * they win for everything inside — without the generated palette being switched off, since a
 * token not named here still resolves from the `palette` seed below.
 *
 * They are `var()` rather than literals so `styles.css` stays the one place the theme lives: its
 * dark half is a `prefers-color-scheme` block, and these inherit straight through it.
 *
 * `$radius` is not a preference but a *collision*. The kit ships `$radius: 6px`, which lands on
 * `body` and would silently have overridden the 10px `:root` corner this stylesheet uses nine
 * times — which is why that one is `--app-radius` there now. Restoring it here also gives the
 * kit's own components the app's corner rather than Cube's.
 */
const TOKENS = {
  "#surface": "var(--bg)",
  "#surface-2": "var(--panel)",
  "#surface-3": "var(--raised)",
  "#border": "var(--line)",
  "#surface-text": "var(--ink)",
  "#surface-text-soft": "var(--muted)",
  "#placeholder": "var(--faint)",
  $radius: "var(--app-radius)",
};

/**
 * Hues for everything `TOKENS` does not name.
 *
 * Seeded with colours rather than numbers because a colour keeps its *tone* — seeded numerically,
 * every accent hue lands at roughly the same lightness and the blue this app actually uses never
 * appears. Glaze derives light, dark and high-contrast from each one, so only the light value is
 * given here even though the stylesheet carries both.
 */
const PALETTE = {
  accent: "#3a5bd9",
  themes: { success: "#2f7d4f", danger: "#b3402f", warning: "#9a6b12" },
};

/**
 * `GlobalStyles` writes the kit's own `body` rule at runtime, so it lands after the bundled
 * stylesheet and wins on equal specificity. These put back what `styles.css` had said — without
 * them the page silently takes the kit's 14px/20px `t3` preset instead of this app's 15px/1.6.
 */
const BODY = {
  background: "var(--bg)",
  color: "var(--ink)",
  fontFamily: "var(--sans)",
  fontSize: "var(--text-lg)",
  lineHeight: "1.6",
  letterSpacing: "normal",
};

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root
      tokens={TOKENS}
      palette={PALETTE}
      bodyStyles={BODY}
      font="var(--sans)"
      monospaceFont="var(--mono)"
    >
      <App />
    </Root>
  </StrictMode>,
);
