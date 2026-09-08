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

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
    return LOOPBACK_HOSTNAMES | {h.lower() for h in settings.effective_allowed_hosts()}


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

# Only meaningful over TLS, and actively harmful on localhost: a browser that
# has seen it once refuses plain http to that host for a year, which would
# break every future dev server on 127.0.0.1. Hence public mode only.
#
# No `preload`. Getting onto the preload list is easy and getting off it takes
# months, and this is a hobby deployment on a subdomain that may well move.
HSTS_HEADER = "max-age=31536000; includeSubDomains"

# The largest request body the API will look at. A chat message is capped at
# 32 000 characters by the schema, so a megabyte is generous for the JSON
# around it — and the instance this runs on has 512 MB of RAM in total, which a
# single unbounded upload would take out.
MAX_BODY_BYTES = 1_048_576

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

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


def _headers_for(path: str) -> list[tuple[bytes, bytes]]:
    """The header set this response should carry, as raw ASGI pairs."""
    api_csp = _json_only_csp(path)
    out: list[tuple[bytes, bytes]] = []
    for header, value in SECURITY_HEADERS.items():
        if header == "Content-Security-Policy" and not api_csp:
            # Swagger keeps its historical exemption; everything else — the
            # SPA — gets the framing rule and nothing that could blank it.
            if path not in DOCS_PATHS:
                out.append((header.lower().encode("latin-1"), DOCUMENT_CSP.encode("latin-1")))
            continue
        out.append((header.lower().encode("latin-1"), value.encode("latin-1")))
    if settings.public_mode:
        out.append((b"strict-transport-security", HSTS_HEADER.encode("latin-1")))
    return out


class HardeningMiddleware:
    """Pure ASGI, deliberately not `BaseHTTPMiddleware`.

    `BaseHTTPMiddleware` runs the app inside an anyio task group and buffers the
    response through a memory stream. For JSON that is invisible; for the SSE
    streams in `api/chat.py` it is not — a client that aborts mid-answer (the
    stop button, a closed tab, a phone losing signal) surfaces as an anyio
    stream error or a bare "No response returned", and the disconnect detection
    sse-starlette relies on is delayed behind the wrapper. Deep research runs up
    to two minutes, so that is a routine event, not an edge case.

    Wrapping `send` instead touches nothing but the response-start message.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        path: str = scope.get("path", "")

        host = _hostname(headers.get("host", "")).lower()
        if host and host not in allowed_hostnames():
            # 421 is the honest status: this server does not answer for that name.
            await JSONResponse(
                {"detail": "Neplatná adresa serveru."},
                status_code=421,
                headers=SECURITY_HEADERS,
            )(scope, receive, send)
            return

        refusal = _oversized(scope, headers, path)
        if refusal is not None:
            await refusal(scope, receive, send)
            return

        extra = _headers_for(path)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw: list[tuple[bytes, bytes]] = list(message.get("headers") or [])
                present = {name.lower() for name, _ in raw}
                raw.extend((n, v) for n, v in extra if n not in present)
                message = {**message, "headers": raw}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _oversized(scope: Scope, headers: Headers, path: str) -> JSONResponse | None:
    """413 for an API write that is too big, or refuses to say how big it is.

    Neither uvicorn nor FastAPI caps a request body, so without this one
    request can decide how much memory the process uses. The declared
    `Content-Length` is the check; a chunked write to the API is refused
    outright rather than read hopefully, because the app's own frontend always
    sends a length (`fetch` with a JSON string body does) and an upload that
    hides its size is exactly the shape being defended against.
    """
    if scope.get("method", "GET") in SAFE_METHODS:
        return None
    if not (path == "/api" or path.startswith("/api/")):
        return None

    declared = headers.get("content-length")
    if declared is None:
        if headers.get("transfer-encoding", "").lower() == "chunked":
            return JSONResponse(
                {"detail": "Požadavek musí uvádět svou velikost."},
                status_code=411,
                headers=SECURITY_HEADERS,
            )
        return None
    try:
        size = int(declared)
    except ValueError:
        return JSONResponse(
            {"detail": "Neplatná hlavička Content-Length."},
            status_code=400,
            headers=SECURITY_HEADERS,
        )
    if size > MAX_BODY_BYTES:
        return JSONResponse(
            {"detail": "Požadavek je příliš velký."},
            status_code=413,
            headers=SECURITY_HEADERS,
        )
    return None
