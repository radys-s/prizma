"""Serving the built frontend from the same process as the API.

This is what makes the deployment single-origin, and single-origin is what
makes the whole thing simple: the frontend already calls relative `/api/...`
URLs, so with one origin there is no CORS preflight, no third-party cookie
question, and no second service to keep awake. On a free tier that also halves
the number of things that can be asleep when Tomáš opens the app.

Mounted only when `frontend/dist` exists. During local development it does not
— Vite serves the app on :5173 and proxies `/api` here — so this module is a
no-op on the machine it was written on, which is exactly right.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BACKEND_DIR

DIST_DIR = BACKEND_DIR.parent / "frontend" / "dist"

# Served from the dist root rather than /assets. Vite writes hashed bundles
# into dist/assets, but the manifest, the service worker and the icons sit
# beside index.html and each needs its own route — a service worker in
# particular is scoped to the directory it is served from, so `/sw.js` can
# control the whole app while `/assets/sw.js` could not.
ROOT_FILES = (
    "manifest.webmanifest",
    "sw.js",
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-512.png",
    "apple-touch-icon.png",
    "robots.txt",
)

# `index.html` must never be cached hard: it is the only file whose name does
# not change between deploys, so a cached copy would keep pointing at the
# hashed bundles of the previous version — which no longer exist.
_HTML_CACHE = "no-cache"
_IMMUTABLE = "public, max-age=31536000, immutable"


def is_built() -> bool:
    return (DIST_DIR / "index.html").is_file()


def _index() -> FileResponse:
    return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": _HTML_CACHE})


class _ImmutableStatic(StaticFiles):
    """`StaticFiles` plus the `Cache-Control` header it does not send.

    Starlette sets only `etag` and `last-modified`, so every returning visitor
    revalidates every bundle — against a free instance that may be asleep. The
    filenames here are content-hashed by Vite, so the file at a given path can
    never change and a year of immutable caching is simply true.

    The service worker's cache-first rule for `/assets/` hides this after the
    first visit, which is exactly why it never showed up in testing.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", _IMMUTABLE)
        return response


def mount(app: FastAPI) -> bool:
    """Attach the SPA routes. Returns whether anything was mounted."""
    if not is_built():
        return False

    assets = DIST_DIR / "assets"
    if assets.is_dir():
        # Vite fingerprints every filename in here, so a given URL's contents
        # can never change — a year of immutable caching is safe and means a
        # returning visitor downloads nothing but the HTML.
        app.mount("/assets", _ImmutableStatic(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return _index()

    # Registered after every router, so a real API route always wins the match.
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """A root file if we have one, otherwise the SPA shell.

        Anything unknown falls through to `index.html` rather than 404ing:
        a client-side route like `/nastaveni` is a real URL to the user, and a
        hard refresh on one has to work.

        `/api/*` is the exception. Without it a mistyped endpoint would return
        the HTML shell with status 200, and the frontend would try to parse a
        web page as JSON and report something incomprehensible — the kind of
        bug that costs an hour to trace back to a missing route.
        """
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Neznámý endpoint.")

        if path in ROOT_FILES:
            file = DIST_DIR / path
            if file.is_file():
                cache = _IMMUTABLE if path.endswith(".png") else _HTML_CACHE
                return FileResponse(file, headers={"Cache-Control": cache})

        return _index()

    return True
