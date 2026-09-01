/**
 * `scroll-behavior: auto !important` in the reduced-motion block only governs
 * scrolls the browser starts on its own. A scroll asked for from JS carries
 * its behaviour in the call and overrides the stylesheet, so the preference
 * has to be read here as well — otherwise the page sliding under the reader
 * is the one animation a motion-sensitive user cannot switch off.
 *
 * Read per call rather than cached: the setting can change mid-session, and
 * a `matchMedia` listener would be more machinery than one lookup is worth.
 */
export function scrollBehavior(): ScrollBehavior {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
