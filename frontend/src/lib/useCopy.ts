import { useCallback, useEffect, useRef, useState } from "react";

/** Clipboard write with a short-lived "copied" flag, shared by the copy buttons. */
export function useCopy(resetAfterMs = 1500) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        return; // blocked in an insecure context — better silent than a fake success
      }
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), resetAfterMs);
    },
    [resetAfterMs]
  );

  return { copied, copy };
}
