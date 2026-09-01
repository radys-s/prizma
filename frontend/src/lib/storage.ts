/**
 * localStorage is a convenience here, never a dependency.
 *
 * A private window, a browser configured to block site data, or a quota error
 * must not stop the app from starting — so every access is wrapped and falls
 * back to the default. Shared by the pickers, the theme and the composer's
 * per-conversation drafts, which is why it lives here rather than in App.tsx.
 */
export function loadSetting(key: string, fallback: string): string {
  try {
    return window.localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

export function saveSetting(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* not worth surfacing — whatever this backs still works for this session */
  }
}

export function clearSetting(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* same as above */
  }
}
