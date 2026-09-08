import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import spa
from app.api import auth, chat, conversations, providers, routing, settings as settings_api
from app.auth.guard import require_auth
from app.auth.hardening import HardeningMiddleware
from app.config import check_public_mode, settings
from app.db import init_db
from app.models import auth as auth_models  # noqa: F401 - registers the auth tables

# Uvicorn si nastaví jen svoje vlastní loggery; kořenový logger zůstane na
# WARNING, a všechno, co aplikace posílá přes log.info(), zmizí. Týká se to
# i řádku o povolených hostech níž — ten měl být jediným vodítkem při smyčce
# 421 a v deploy logu se nikdy neobjevil. Loggerům uvicornu to nevadí: mají
# vlastní handler a `propagate=False`, takže se řádky nezdvojí.
#
# `getLevelName` proto, že překlep v LOG_LEVEL by jinak shodil import celičké
# aplikace — nastavení hlasitosti logu nesmí být důvod, proč server nenabootuje.
_level = logging.getLevelName(os.getenv("LOG_LEVEL", "INFO").strip().upper())
logging.basicConfig(
    level=_level if isinstance(_level, int) else logging.INFO,
    format="%(levelname)s:     %(name)s - %(message)s",
)

# litellm si tiskne vlastní obarvený řádek ke každému volání *a* pošle totéž
# kořenovému loggeru. Jakmile kořen začne pouštět INFO, je z jedné odpovědi
# osm řádků o tom, který model se právě volá, dvakrát. Varování a chyby
# prochází dál — mizí jen ten duplík.
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before the database, before anything: a public server missing its master
    # key or its host allowlist is broken in ways that only surface later and in
    # the dark — an unreadable API key looks like "the provider is down", a
    # missing allowlist looks like "the site is down". Refusing to boot says
    # which one it is, once, in the deploy log.
    check_public_mode(settings)

    if settings.public_mode:
        # Named once, on purpose. A hostname mismatch makes every request 421 —
        # the health check included — and the only clue in the log is
        # "Neplatná adresa serveru" with no mention of what was expected.
        log.info("Server odpovídá na: %s", ", ".join(settings.effective_allowed_hosts()))
        if not settings.contact_url.strip():
            # `websearch` uses it as the User-Agent contact for Wikipedia's
            # API. Without it that leg is skipped and deep research quietly
            # degrades to arXiv only, with no error anywhere.
            log.warning("CONTACT_URL není nastavený — hledání ve Wikipedii bude vypnuté.")

    init_db()
    yield


# Swagger is a genuinely useful thing to have on a laptop and a genuinely
# strange thing to publish: every route behind it needs a session, but an
# anonymous visitor to a private single-user app should not be handed an
# interactive map of its API. Off on the server, on locally.
_docs = {} if not settings.public_mode else {
    "docs_url": None,
    "redoc_url": None,
    "openapi_url": None,
}

app = FastAPI(title="Prizma BYOK Router", lifespan=lifespan, **_docs)

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(settings.allowed_origins()),
    allow_credentials=True,  # the session cookie has to survive the proxy hop
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last, so it sits *outermost*: the Host check runs before anything else
# looks at the request (CORS preflights included), and the security headers get
# stamped onto every response on the way out.
app.add_middleware(HardeningMiddleware)

# Auth is the only unauthenticated surface; it does its own loopback/Origin
# check. Everything below requires a session *and* a CSRF token on writes.
app.include_router(auth.router)

protected = [Depends(require_auth)]
app.include_router(providers.router, dependencies=protected)
app.include_router(conversations.router, dependencies=protected)
app.include_router(chat.router, dependencies=protected)
app.include_router(routing.router, dependencies=protected)
app.include_router(settings_api.router, dependencies=protected)


@app.get("/api/health")
def health():
    """Unauthenticated on purpose — it reveals nothing but liveness."""
    return {"ok": True}


# Last, after every router: the SPA's catch-all would otherwise swallow routes
# that are declared below it. A no-op during local development, where
# `frontend/dist` does not exist and Vite serves the app on :5173.
spa.mount(app)
