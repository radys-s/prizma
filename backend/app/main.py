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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before the database, before anything: a public server missing its master
    # key or its host allowlist is broken in ways that only surface later and in
    # the dark — an unreadable API key looks like "the provider is down", a
    # missing allowlist looks like "the site is down". Refusing to boot says
    # which one it is, once, in the deploy log.
    check_public_mode(settings)
    init_db()
    yield


app = FastAPI(title="Prizma BYOK Router", lifespan=lifespan)

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
