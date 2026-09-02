import { useEffect, useState } from "react";

/**
 * Registers the service worker and reports when a newer one is waiting.
 *
 * The worker deliberately does not call `skipWaiting()` on its own: swapping
 * the bundle while an answer is streaming would tear the page out from under a
 * conversation. So the new version sits in `waiting` until the user accepts the
 * toast, and only then do we tell it to take over and reload.
 *
 * Dev is skipped entirely — Vite serves unhashed modules over HMR, and a worker
 * caching them would fight the dev server for no benefit.
 */
export function useServiceWorker() {
  const [waiting, setWaiting] = useState<ServiceWorker | null>(null);

  useEffect(() => {
    if (import.meta.env.DEV) return;
    if (!("serviceWorker" in navigator)) return;

    let cancelled = false;

    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        if (cancelled) return;
        if (reg.waiting) setWaiting(reg.waiting);
        reg.addEventListener("updatefound", () => {
          const next = reg.installing;
          if (!next) return;
          next.addEventListener("statechange", () => {
            // `installed` with a controller already present means this is an
            // update rather than the very first install — the first install has
            // nothing to replace and should not prompt.
            if (next.state === "installed" && navigator.serviceWorker.controller) {
              setWaiting(next);
            }
          });
        });
      })
      .catch(() => {
        /* No worker is a degraded install, not a broken app. */
      });

    return () => {
      cancelled = true;
    };
  }, []);

  function applyUpdate() {
    if (!waiting) return;
    // The reload is driven by `controllerchange` rather than fired straight
    // away: reloading before the new worker has claimed the page would just
    // load the old bundle again.
    navigator.serviceWorker.addEventListener("controllerchange", () => window.location.reload(), {
      once: true,
    });
    waiting.postMessage("skip-waiting");
    setWaiting(null);
  }

  return { updateReady: waiting !== null, applyUpdate };
}
