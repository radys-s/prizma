"""Transport-level hardening applied to every response.

Two things live here, both cheap and both covering an angle the auth guard
does not:

**Host allowlist.** The `Origin` check stops a page at `evil.example` from
driving this API — but DNS rebinding gets around it: the attacker points
`evil.example` at `127.0.0.1`, so the browser now considers the request
*same-origin* and sends no cross-origin `Origin` at all. What it cannot change
is the `Host` header, which still says `evil.example`. Pinning `Host` to the
names we actually serve on closes that.

**Response headers.** The API returns JSON only, so it should never be sniffed
as HTML, framed, or allowed to load anything. `frame-ancestors 'none'` plus
`X-Frame-Options` means an attacker cannot put the API in an iframe and read
it, and `Referrer-Policy` keeps conversation URLs out of outbound requests.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

# The API is only ever addressed by these. A port is allowed after the name.
LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


def allowed_hostnames() -> set[str]:
    """Loopback plus whatever the deployment answers for.

    Read per request rather than frozen at import so a harness can set
    `settings.allowed_hosts` and see it take effect — and so a deployment that
    changes its hostname needs a restart, not a code change.

    Loopback stays allowed even in public mode: Render's own health checks and
    any `docker exec` curl arrive that way, and a name that resolves only to the
    machine itself gives an attacker nothing.
    """
    return LOOPBACK_HOSTNAMES | {h.strip().lower() for h in settings.allowed_hosts if h.strip()}


# Kept for anything that imported the old constant. Prefer `allowed_hostnames()`.
ALLOWED_HOSTNAMES = LOOPBACK_HOSTNAMES

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # The API serves JSON, so everything is denied; `frame-ancestors` is the
    # part that actually matters here (X-Frame-Options is the older spelling).
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
}

# What a non-API response gets instead. `frame-ancestors` cannot be set from a
# `<meta>` tag — the spec ignores it there — so the SPA's anti-framing rule has
# to arrive as a real header even though the rest of its policy comes from the
# meta tag `vite.config.ts` injects at build time.
DOCUMENT_CSP = "frame-ancestors 'none'"

# Swagger UI is a real HTML page that loads its own assets, so the JSON-only
# CSP would blank it. Everything except the CSP still applies to it.
DOCS_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}


def _hostname(raw: str) -> str:
    """Strips the port, keeping bracketed IPv6 literals intact."""
    host = raw.strip()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _json_only_csp(path: str) -> bool:
    """Whether the strict `default-src 'none'` policy applies to this response.

    It applies to the API and to nothing else. Once this process also serves
    the built SPA, stamping it onto `index.html` would render a blank page —
    the policy forbids loading the very scripts and stylesheets the app is
    made of. The document gets its own, looser policy from the `<meta>` tag
    `vite.config.ts` injects, plus the framing headers below, which are the
    part that actually defends anything here.
    """
    if path in DOCS_PATHS:
        return False
    return path == "/api" or path.startswith("/api/")


class HardeningMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = _hostname(request.headers.get("host", "")).lower()
        if host and host not in allowed_hostnames():
            # 421 is the honest status: this server does not answer for that name.
            return JSONResponse(
                {"detail": "Neplatná adresa serveru."},
                status_code=421,
                headers=SECURITY_HEADERS,
            )

        response: Response = await call_next(request)
        api_csp = _json_only_csp(request.url.path)
        for header, value in SECURITY_HEADERS.items():
            if header == "Content-Security-Policy" and not api_csp:
                # Swagger keeps its historical exemption; everything else — the
                # SPA — gets the framing rule and nothing that could blank it.
                if request.url.path not in DOCS_PATHS:
                    response.headers.setdefault(header, DOCUMENT_CSP)
                continue
            response.headers.setdefault(header, value)
        return response
