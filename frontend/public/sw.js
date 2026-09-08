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
// The hashed bundles are not listed here — they cannot be, their names change
// every build — but they are not left to chance either; see `precache()`.
const PRECACHE = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];

/**
 * The shell, and the code the shell needs to be an app.
 *
 * Caching only the document was a hole big enough to see through: `index.html`
 * references `/assets/index-<hash>.js` and its stylesheet by name, and until
 * the fetch handler had happened to see those exact requests, they were in no
 * cache at all. Open the app with no server behind it — offline, or in the
 * half minute after logon before the server has finished starting — and the
 * document came out of the cache, its bundles came out of nowhere, and the
 * result was a blank white page. Nothing retried, because from the browser's
 * point of view the navigation had succeeded.
 *
 * So the names are read out of the document that was just fetched. That keeps
 * the list honest by construction: it cannot go stale against a build, because
 * it comes from the build.
 */
async function precache(cache) {
  await cache.addAll(PRECACHE);
  const res = await fetch("/", { cache: "reload" });
  if (!res.ok) return;
  const html = await res.text();
  const assets = new Set(
    [...html.matchAll(/["'](\/assets\/[A-Za-z0-9._-]+)["']/g)].map((m) => m[1])
  );
  if (assets.size) await cache.addAll([...assets]);
}

self.addEventListener("install", (event) => {
  // A failed install must not leave a worker that serves nothing. Swallowing
  // here means the fetch handler fills the cache lazily instead, which is
  // where this started — but as a fallback rather than as the whole plan.
  event.waitUntil(
    caches.open(SHELL).then((cache) => precache(cache)).catch(() => undefined)
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
          fetch(request)
            .then((res) => {
              if (res.ok) {
                const copy = res.clone();
                caches.open(SHELL).then((cache) => cache.put(request, copy));
              }
              return res;
            })
            // Cache miss plus dead network. `respondWith` on a rejected promise
            // ends the same way for the page either way — a failed subresource —
            // but an explicit error keeps it out of the unhandled-rejection log,
            // where it looked like a bug in the worker rather than a missing
            // server.
            .catch(() => Response.error())
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
