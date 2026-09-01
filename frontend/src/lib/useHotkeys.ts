import { useEffect, useRef } from "react";

export interface Hotkey {
  /** `event.key`, compared case-insensitively: "k", "Escape", "ArrowUp". */
  key: string;
  /** Ctrl on Windows/Linux, ⌘ on a Mac — the same shortcut either way. */
  ctrl?: boolean;
  shift?: boolean;
  /**
   * Fire even while the caret is in a text field. Off by default: a bare "k"
   * shortcut that steals a keystroke from the composer is worse than no
   * shortcut at all. Ctrl-combinations set this because nothing types them.
   */
  inFields?: boolean;
  /**
   * Return `false` to decline the key — the default action then still
   * happens. `↑` needs this: it is only a shortcut while the composer is
   * empty, and the handler is the only thing that can know that.
   */
  handler: (event: KeyboardEvent) => void | boolean;
}

function inTextField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || el.isContentEditable === true;
}

/**
 * Window-level keyboard shortcuts.
 *
 * The list is held in a ref so callers can pass a fresh array every render —
 * which they will, because the handlers close over component state — without
 * tearing down and re-adding the listener each time.
 */
export function useHotkeys(hotkeys: Hotkey[]): void {
  const ref = useRef(hotkeys);
  ref.current = hotkeys;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // A dead key or an IME composition is not a shortcut.
      if (event.isComposing) return;
      const typing = inTextField(event.target);

      for (const hotkey of ref.current) {
        const wantsMod = hotkey.ctrl === true;
        if (wantsMod !== (event.ctrlKey || event.metaKey)) continue;
        if ((hotkey.shift === true) !== event.shiftKey) continue;
        if (event.key.toLowerCase() !== hotkey.key.toLowerCase()) continue;
        if (typing && hotkey.inFields !== true && !wantsMod) continue;
        // preventDefault after the call, so a handler that declines the key
        // leaves the browser's own behaviour intact.
        if (hotkey.handler(event) === false) continue;
        event.preventDefault();
        return;
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
