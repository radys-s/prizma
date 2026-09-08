"""Test environment.

Every environment variable here is set **before** `app.config` is imported,
because `Settings` reads the environment once at import time and `app.db`
builds its engine off the result. Importing anything from `app` at module
scope in this file would freeze the developer's own configuration into the
tests — including their real database.

The suite runs in public mode on purpose. That is the configuration being
shipped, it is the only one where the Host allowlist, `Secure` cookies and the
CSRF requirement are actually live, and those are most of what is being tested.
"""

import os
import shutil
from pathlib import Path

# A project-local scratch directory. Windows pytest cannot always clean up
# `tmp_path` (PermissionError WinError 5 while the SQLite handle is open), and
# a stray temp file inside the repo is easier to reason about than one that
# vanished into the user profile.
_TMP = Path(__file__).resolve().parent / "_tmp"
shutil.rmtree(_TMP, ignore_errors=True)
_TMP.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["PUBLIC_MODE"] = "true"
os.environ["SECRET_KEY"] = "0" * 64  # 32 bytes of hex, never a real key
os.environ["ALLOWED_HOSTS"] = '["testserver"]'
os.environ["PUBLIC_BASE_URL"] = "https://testserver"
os.environ["OWNER_EMAIL"] = "owner@example.com"
os.environ["CORS_ORIGINS"] = '["https://testserver"]'

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    """A client whose requests look like a browser on the deployed origin.

    `https` in the base URL is load-bearing: in public mode the session and
    OAuth cookies are `Secure`, and httpx will not store — let alone resend —
    a `Secure` cookie set over plain http. Testing against http would silently
    turn every cookie-dependent assertion into a test of nothing.
    """
    from app.main import app

    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def db():
    from app.db import engine
    from sqlmodel import Session

    with Session(engine) as session:
        yield session
