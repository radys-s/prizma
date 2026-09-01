import { useCallback, useEffect, useRef, useState } from "react";
import { scrollBehavior } from "./motion";

/** How close to the bottom still counts as "following the stream". */
const NEAR_BOTTOM_PX = 80;

/**
 * Keeps a scroll container pinned to the bottom while the user is already
 * there, and lets go the moment they scroll up to read something — the old
 * unconditional scrollTo fought the user during long answers.
 */
export function useAutoScroll(dependency: unknown) {
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

  useEffect(() => {
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
