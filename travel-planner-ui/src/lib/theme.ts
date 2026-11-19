import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
export const THEME_KEY = "trip-planner-theme"; // keep in sync with the inline script in index.html

const isChoice = (v: unknown): v is ThemeChoice => v === "system" || v === "light" || v === "dark";

export function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return isChoice(v) ? v : "system";
  } catch {
    return "system";
  }
}

const darkQuery = () => window.matchMedia("(prefers-color-scheme: dark)");

/** Resolve a choice to the theme that is actually shown. */
export function resolveTheme(choice: ThemeChoice): "light" | "dark" {
  return choice === "system" ? (darkQuery().matches ? "dark" : "light") : choice;
}

/** The saved theme choice, applied to <html data-theme>. "system" follows the operating system live. */
export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);

  useEffect(() => {
    const apply = () => { document.documentElement.dataset.theme = resolveTheme(choice); };
    apply();
    if (choice !== "system") return;
    const q = darkQuery();
    q.addEventListener("change", apply);
    return () => q.removeEventListener("change", apply);
  }, [choice]);

  const update = useCallback((c: ThemeChoice) => {
    setChoice(c);
    try { localStorage.setItem(THEME_KEY, c); } catch { /* private mode: the choice lasts for this visit only */ }
  }, []);

  return [choice, update];
}
