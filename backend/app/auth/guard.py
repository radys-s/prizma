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

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from app.auth import core
from app.config import settings
from app.db import get_session
from app.models.auth import AuthSession

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def enforce_origin(request: Request) -> None:
    """Layers 1 and 2. Raises HTTPException, never returns a value."""
    allowed = settings.allowed_origins()
    origin = request.headers.get("origin")

    if not settings.public_mode:
        client_host = request.client.host if request.client else None
        if client_host not in LOOPBACK_HOSTS:
            raise HTTPException(status_code=403, detail="Povoleno jen z tohoto počítače.")
        if origin is not None and origin not in allowed:
            raise HTTPException(status_code=403, detail="Nepovolený Origin.")
        return

    if origin is not None:
        if origin not in allowed:
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
