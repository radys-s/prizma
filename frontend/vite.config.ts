import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * Content-Security-Policy for the page itself. The backend hardens the API;
 * this hardens the document that talks to it — an injected `<script>` or a
 * remote pixel would otherwise run with the session cookie right there.
 *
 * Dev has to be looser than the build: the React Fast Refresh preamble is an
 * inline module script and HMR needs a WebSocket, neither of which exists in
 * the production bundle. `'unsafe-inline'` on styles stays in both, because
 * Vite injects imported CSS as inline `<style>` in dev and highlight.js themes
 * arrive the same way.
 *
 * `frame-ancestors` is deliberately absent: browsers ignore it when it arrives
 * in a `<meta>` element, so listing it here would only look like protection.
 * It is sent as a real header instead — by the dev server below, and in
 * production by whatever serves `dist/`.
 */
function csp(isDev: boolean, inlineHashes: string[] = []): string {
  // In production every inline script is allowed by its own hash instead —
  // see `inlineScriptHashes`.
  const scriptSrc = isDev
    ? "script-src 'self' 'unsafe-inline'"
    : ["script-src 'self'", ...inlineHashes.map((h) => `'${h}'`)].join(" ");
  return [
    "default-src 'self'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src 'self'${isDev ? " ws://localhost:5173 ws://127.0.0.1:5173" : ""}`,
    // The service worker and the manifest are same-origin files, but
    // `default-src` does not cover either directive — without these two the
    // worker never registers and the install prompt never appears.
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
}

/**
 * Every inline `<script>` in the document, hashed the way CSP wants it.
 *
 * Without this the build shipped `script-src 'self'` over an index.html that
 * contains one — the pre-paint theme script. It worked on the laptop, where
 * dev adds `'unsafe-inline'`, and was blocked on the server: a CSP violation
 * on every page load plus the exact flash of the wrong theme that the script
 * exists to prevent. Hashing beats an allowlist here because it cannot drift:
 * edit the script and the hash follows.
 *
 * **Line endings are load-bearing.** `index.html` is CRLF in a Windows working
 * copy and Vite hands the file through unchanged, but the HTML parser
 * normalises every CRLF to a bare LF *before* the script body is hashed for
 * CSP. Hashing the bytes as they sit on disk therefore produced a hash the
 * browser never computes — the policy shipped, the script was blocked anyway,
 * and the only place it showed was a production build opened in a browser.
 * Normalising first makes the build byte-identical on Windows and Linux.
 */
function inlineScriptHashes(html: string): string[] {
  const hashes: string[] = [];
  const inline = /<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  for (const match of html.matchAll(inline)) {
    const body = match[1]?.replace(/\r\n?/g, "\n");
    if (!body) continue;
    hashes.push(`sha256-${createHash("sha256").update(body, "utf8").digest("base64")}`);
  }
  return hashes;
}

function cspPlugin(): Plugin {
  let isDev = false;
  return {
    name: "prizma-csp",
    configResolved: (config) => {
      isDev = config.command === "serve";
    },
    transformIndexHtml: {
      // `post` so the hashes cover the document Vite actually emits, including
      // anything a later plugin injected into it.
      order: "post",
      handler: (html: string) =>
        html.replace(
          "<head>",
          `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp(
            isDev,
            inlineScriptHashes(html)
          )}" />`
        ),
    },
  };
}

/**
 * Stamps a build id into the service worker's cache name.
 *
 * `sw.js` lives in `public/`, so Vite copies it verbatim and never transforms
 * it — which is why its version was a hardcoded constant that never changed.
 * The `activate` handler deletes every cache whose name does not start with
 * the current VERSION, so a constant meant it deleted nothing, ever, and a
 * shell cached from one deploy could outlive several more.
 */
function swVersionPlugin(): Plugin {
  const buildId = Date.now().toString(36);
  return {
    name: "prizma-sw-version",
    generateBundle(_options, bundle) {
      void bundle;
      this.emitFile({
        type: "asset",
        fileName: "sw.js",
        source: readFileSync("public/sw.js", "utf8").replace("__BUILD_ID__", buildId),
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), cspPlugin(), swVersionPlugin()],
  build: {
    rollupOptions: {
      output: {
        // Markdown + syntax highlighting is the bulk of the bundle and changes
        // far less often than the app itself — keep it in its own chunk.
        manualChunks: {
          markdown: ["react-markdown", "remark-gfm", "rehype-highlight"],
        },
      },
    },
  },
  server: {
    port: 5173,
    // Framing protection has to travel as a header — see the note on csp().
    headers: {
      "Content-Security-Policy": "frame-ancestors 'none'",
      "X-Frame-Options": "DENY",
    },
    // Fail loudly instead of drifting to 5174: the backend only trusts an
    // Origin on 5173, so a silent port change would 403 every key write.
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
