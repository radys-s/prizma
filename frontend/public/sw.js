/**
 * Prizma's service worker. Hand-written on purpose — `vite-plugin-pwa` would
 * pull in Workbox and precache-manifest generation to do rather less than this
 * file does, and the one rule that actually matters here is a rule about what
 * *not* to touch.
 *
 * That rule: `/api/*` is never intercepted. Not cached, not retried, not
 * queued. A stale conversation list would be confusing; a replayed POST that
 * spends a provider quota or sends a message twice would be a real bug. The
 * network is the only source of truth for the API, and when it is down the app
 * should say so rather than lie from a cache.
 *
 * Everything else is the app shell, which is immutable by filename (Vite hashes
 * `/assets/*`), so it can be cached hard.
 */

// Replaced at build time by the `prizma-sw-version` plugin in vite.config.ts.
// It has to change every deploy, otherwise the `activate` cleanup below never
// evicts anything and a stale shell can outlive several releases.
const VERSION = "prizma-__BUILD_ID__";
const SHELL = `${VERSION}-shell`;

// Enough to open the app offline: the document, the manifest and the icons.
// The hashed bundles are not listed — they are added on first use instead, so
// this list never goes stale against a build.
const PRECACHE = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(PRECACHE)).catch(() => undefined)
  );
  // No `skipWaiting()` here. A new worker taking over mid-stream would swap the
  // bundle under a conversation that is still being written. The page asks for
  // the swap when the user accepts the update toast — see the message handler.
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Cross-origin (OAuth redirects, provider signup links) and the API are the
  // browser's business, not ours.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  // Hashed assets: the filename *is* the version, so a hit is always correct.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(SHELL).then((cache) => cache.put(request, copy));
            }
            return res;
          })
      )
    );
    return;
  }

  // The document, and anything else same-origin: network first so a deploy is
  // picked up immediately, cache only as the offline fallback.
  if (request.mode === "navigate" || request.destination === "document") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          // Only a real page becomes the offline shell. Without the `res.ok`
          // check, a 421 from the host allowlist, a 500, or a hosting
          // "service unavailable" interstitial gets written over "/" and is
          // then what an offline user sees forever.
          if (res.ok && request.method === "GET") {
            const copy = res.clone();
            caches.open(SHELL).then((cache) => cache.put("/", copy));
          }
          return res;
        })
        .catch(() => caches.match("/").then((hit) => hit || Response.error()))
    );
    return;
  }

  event.respondWith(
    fetch(request).catch(() => caches.match(request).then((hit) => hit || Response.error()))
  );
});
