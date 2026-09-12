/**
 * The theme preference: light, dark, or whatever the OS says.
 *
 * Almost nothing happens here, and that is deliberate. `tokens.css` builds the
 * whole palette out of `light-dark()`, so the browser resolves "system" on its
 * own — this module only ever stamps or removes one attribute on <html>. The
 * same code runs inline in `index.html` before first paint; keep the two in
 * step if the storage key or the attribute name ever changes.
 */

export type Theme = "light" | "dark" | "system";

export const THEME_KEY = "prizma.theme";

/** What the browser actually paints when the preference is "system". */
export type Resolved = "light" | "dark";

const THEME_COLOR: Record<Resolved, string> = {
  light: "#f0eee6",
  dark: "#1f1e1d",
};

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}

export function loadTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    return isTheme(stored) ? stored : "system";
  } catch {
    // Private window or blocked storage — the OS preference is a fine default.
    return "system";
  }
}

export function saveTheme(theme: Theme): void {
  try {
    if (theme === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Not being able to remember the choice is not a reason to refuse it.
  }
}



/**
 * Puts the choice on the document.
 *
 * "system" *removes* the attribute rather than writing the resolved value:
 * that hands the decision back to CSS, so the app keeps following the OS live
 * even if the listener below is ever removed.
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;

  // The two media-scoped <meta name="theme-color"> tags in index.html cover
  // the system case; a forced theme needs an unscoped one to override them.
  const existing = document.querySelector<HTMLMetaElement>(
    'meta[name="theme-color"]:not([media])'
  );
  if (theme === "system") {
    existing?.remove();
    return;
  }
  const meta = existing ?? document.createElement("meta");
  meta.name = "theme-color";
  meta.content = THEME_COLOR[theme];
  if (!existing) document.head.appendChild(meta);
}

/** Calls back whenever the OS flips. Returns the unsubscribe. */
export function watchSystem(onChange: (dark: boolean) => void): () => void {
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!query) return () => {};
  const handler = (event: MediaQueryListEvent) => onChange(event.matches);
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}
