import { useEffect, useState } from "react";

/**
 * Light, dark, or whatever the system says — the one preference the portal keeps in the browser.
 *
 * The stylesheet keys its dark tokens on `<html data-schema="dark">` — the UI kit's attribute,
 * which its own palette also obeys — so this module's whole job is to keep that attribute right.
 * `index.html` sets it once before first paint, from the same key and the same rule, because a
 * module script runs after the page has already drawn in the wrong colours.
 *
 * Stored in `localStorage` rather than on the server: it is a property of this browser, not of
 * the deployment, and a portal opened on a second machine should follow that machine.
 */
export type Theme = "auto" | "light" | "dark";

const KEY = "analyst-theme";
const SYSTEM = window.matchMedia("(prefers-color-scheme: dark)");

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    // Storage can be refused outright (a locked-down profile); the system's answer still works.
    return "auto";
  }
}

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "auto" && SYSTEM.matches);
  document.documentElement.dataset.schema = dark ? "dark" : "light";
}

export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(stored);

  useEffect(() => {
    apply(theme);
    if (theme !== "auto") return;
    // Only "auto" follows the system, and it has to follow it live: a machine that switches to
    // dark at sunset should take an open portal with it.
    const follow = () => apply("auto");
    SYSTEM.addEventListener("change", follow);
    return () => SYSTEM.removeEventListener("change", follow);
  }, [theme]);

  const choose = (next: Theme) => {
    try {
      if (next === "auto") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // Not remembered across reloads, but it still applies to this page.
    }
    setTheme(next);
  };

  return [theme, choose];
}
