import { useEffect, useState } from "react";

/**
 * Keeps `<html data-schema>` (the UI kit's dark-mode attribute) in line with the chosen theme.
 * `index.html` repeats this rule before first paint, so the two must agree.
 */
export type Theme = "auto" | "light" | "dark";

const KEY = "analyst-theme";
const SYSTEM = window.matchMedia("(prefers-color-scheme: dark)");

function stored(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "auto";
  } catch {
    // Storage can be refused (a locked-down profile).
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
    // "auto" follows the system live.
    const follow = () => apply("auto");
    SYSTEM.addEventListener("change", follow);
    return () => SYSTEM.removeEventListener("change", follow);
  }, [theme]);

  const choose = (next: Theme) => {
    try {
      if (next === "auto") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // Not remembered, but still applies to this page.
    }
    setTheme(next);
  };

  return [theme, choose];
}
