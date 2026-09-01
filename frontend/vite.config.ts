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
function csp(isDev: boolean): string {
  return [
    "default-src 'self'",
    `script-src 'self'${isDev ? " 'unsafe-inline'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    `connect-src 'self'${isDev ? " ws://localhost:5173 ws://127.0.0.1:5173" : ""}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
}

function cspPlugin(): Plugin {
  let isDev = false;
  return {
    name: "prizma-csp",
    configResolved: (config) => {
      isDev = config.command === "serve";
    },
    transformIndexHtml: (html) =>
      html.replace(
        "<head>",
        `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp(isDev)}" />`
      ),
  };
}

export default defineConfig({
  plugins: [react(), cspPlugin()],
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
