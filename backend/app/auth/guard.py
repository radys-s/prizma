"""The request gate: where every API call proves it's allowed.

Three independent layers, because each one covers a hole the others don't:

1. **Loopback peer check** — refuses anything not originating on this machine.
2. **Origin allowlist** — a browser is forced to send `Origin` on cross-origin
   requests and a page cannot forge it, so this stops a random website from
   driving this API while the user is logged in.
3. **Session cookie + CSRF token** — this is the one that's new. Before it, any
   other process running as the user could POST to :8000 with no `Origin`
   header at all and read or overwrite the key configuration. Now it also
   needs a session token it can only get by knowing the passcode.

`SAFE_METHODS` skip the CSRF check because they must not change state; every
mutating verb has to echo the CSRF cookie back in a header, which a
cross-origin caller cannot read.

**On a public server layer 1 is dropped**, because there it would reject every
real visitor — including Tomáš. That is not a hole being opened so much as a
layer that only ever made sense on a laptop: it answered "is this request from
this machine?", and on a server the honest answer is always no. Layers 2 and 3
were never leaning on it. What replaces it is that the Origin allowlist stops
being advisory: locally a request with *no* `Origin` at all is fine (curl, a
script, the OS), whereas in public mode a state-changing request without a
recognised Origin is refused, because on the internet the only legitimate
caller is the SPA and browsers always send one.
"""

import hmac
import ipaddress

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.auth import core
from app.config import settings
from app.db import get_session
from app.models.auth import AuthSession

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_own_origin(request: Request, origin: str) -> bool:
    """Whether `origin` is this very server, as the browser addressed it.

    The static allowlist cannot cover the case that matters most in practice:
    `spa.py` mounts the built frontend into this same app, so when Prizma is
    started by `aplikace.exe` the page lives on `http://127.0.0.1:8000` — a
    port picked at run time from a free range. Nothing can write that down in
    advance, and without this check the first `POST /api/auth/login` from the
    launcher answered "Nepovolený Origin", which reads exactly like a wrong
    passcode. It was the one way of opening the app that could not log in.

    Comparing against the request's own `Host` gives nothing away. A page on
    another site cannot make the two agree: the browser fills `Origin` from the
    page it is running and `Host` from the URL being fetched, so a cross-site
    request always arrives with them disagreeing. `Host` is not free-form
    either — `HardeningMiddleware` has already checked it against the hostname
    allowlist before any of this runs.

    Both schemes are accepted because the scheme the browser used is not
    visible here: behind Render's proxy the app itself speaks plain HTTP. The
    looseness costs nothing, since the host still has to be ours, and ours is
    only reachable over TLS.
    """
    host = request.headers.get("host")
    if not host:
        return False
    return origin in {f"http://{host}", f"https://{host}"}


def _is_lan_preview_peer(client_host: str | None) -> bool:
    """Whether this peer is a device on the local network, during a LAN preview.

    Off unless `LAN_PREVIEW` is set, which only `mobile/lan-preview.ps1` does —
    see the comment on the setting for why the hole exists at all. The point of
    doing it here rather than widening `LOOPBACK_HOSTS` is that the widening
    stays visible: one named function, one flag, and a range check that cannot
    accidentally admit a public address.

    Parsed as an address rather than string-matched on `192.168.`: `is_private`
    covers 10/8 and 172.16/12 too, and link-local IPv6 (`fe80::`) is how a
    phone on modern Wi-Fi often shows up. Anything unparseable is refused,
    because a hostname here would mean something upstream is rewriting the
    peer and the answer is then not knowable.
    """
    if not settings.lan_preview or not client_host:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return addr.is_private and not addr.is_loopback


def enforce_origin(request: Request) -> None:
    """Layers 1 and 2. Raises HTTPException, never returns a value."""
    allowed = settings.allowed_origins()
    origin = request.headers.get("origin")

    if not settings.public_mode:
        client_host = request.client.host if request.client else None
        if client_host not in LOOPBACK_HOSTS and not _is_lan_preview_peer(client_host):
            raise HTTPException(status_code=403, detail="Povoleno jen z tohoto počítače.")
        if origin is not None and origin not in allowed and not _is_own_origin(request, origin):
            raise HTTPException(status_code=403, detail="Nepovolený Origin.")
        return

    if origin is not None:
        if origin not in allowed and not _is_own_origin(request, origin):
            raise HTTPException(status_code=403, detail="Nepovolený Origin.")
        return

    # No Origin header, in public mode. Safe methods are let through — a plain
    # `GET /api/health` from a monitor or a curl has no Origin and changes
    # nothing. Anything that writes must prove where it came from, because the
    # CSRF token alone cannot: a non-browser client that has somehow obtained
    # the session cookie would also have the CSRF cookie sitting next to it.
    if request.method not in SAFE_METHODS:
        raise HTTPException(status_code=403, detail="Chybí Origin.")




def require_auth(
    request: Request,
    session: Session = Depends(get_session),
) -> AuthSession:
    """Layer 3. Attach to every router that touches user data."""
    enforce_origin(request)

    token = request.cookies.get(core.SESSION_COOKIE)
    auth = core.resolve_session(session, token)
    if auth is None:
        raise HTTPException(status_code=401, detail="Nepřihlášeno.")

    if request.method not in SAFE_METHODS:
        sent = request.headers.get(core.CSRF_HEADER, "")
        if not hmac.compare_digest(sent, auth.csrf_token):
            raise HTTPException(status_code=403, detail="Chybí nebo nesedí CSRF token.")

    return auth
