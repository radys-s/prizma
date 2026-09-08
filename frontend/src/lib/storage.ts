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

/**
 * Drops every key with this prefix. Used to clear composer drafts on sign-out.
 *
 * Unsent message text is the one thing in localStorage that is actual content
 * rather than a preference, and it survived logout — half-typed prompts left
 * on a shared or borrowed device after the session was ended on purpose.
 */
export function clearSettingsWithPrefix(prefix: string): void {
  try {
    const doomed: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key && key.startsWith(prefix)) doomed.push(key);
    }
    doomed.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    /* same as above */
  }
}
