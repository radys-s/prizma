import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { scrollBehavior } from "./motion";

/** How close to the bottom still counts as "following the stream". */
const NEAR_BOTTOM_PX = 80;

/**
 * Keeps a scroll container pinned to the bottom while the user is already
 * there, and lets go the moment they scroll up to read something — the old
 * unconditional scrollTo fought the user during long answers.
 *
 * `resetOn` je identita vlákna. Bez něj si přepnutí konverzace nesla odepnutí
 * z té předchozí: kdo si v jednom vlákně odroloval nahoru, otevřel to další
 * na začátku místo u poslední zprávy.
 */
export function useAutoScroll(dependency: unknown, resetOn?: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setPinned(distance <= NEAR_BOTTOM_PX);
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Nové vlákno se otevírá dole, ať byl uživatel v tom minulém kdekoli.
  useLayoutEffect(() => {
    setPinned(true);
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [resetOn]);

  // `useLayoutEffect`, ne `useEffect`: pasivní efekt zapíše `scrollTop` až po
  // vykreslení, takže prohlížeč nejdřív ukáže vyšší obsah a teprve pak skočí.
  // Při rychlosti streamu to je vidět jako trhání.
  useLayoutEffect(() => {
    if (!pinned) return;
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [dependency, pinned]);

  const scrollToBottom = useCallback(() => {
    const el = ref.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: scrollBehavior() });
    setPinned(true);
  }, []);

  return { ref, pinned, scrollToBottom };
}
