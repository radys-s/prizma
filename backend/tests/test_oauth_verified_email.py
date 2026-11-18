"""Only an address the provider has verified may claim the account.

`identities.owner_allows()` decides who creates the single account by the
`email` claim of the first sign-in. The OIDC path passed that claim through
without looking at `email_verified`, although the GitHub path already insists
on a verified address for the same reason: an unverified one is text the user
typed. Microsoft's `email` claim, for one, is not verified at all for accounts
from other tenants.
"""

import asyncio
import base64
import json
import time
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from sqlmodel import Session, select

from app.auth import oauth, ratelimit
from app.config import settings
from app.db import engine, init_db
from app.models.auth import AppUser, AuthIdentity, AuthSession

OWNER = "owner@example.com"
CLIENT_ID = "test-client-id"


def _wipe() -> None:
    init_db()  # the tables exist only once something has started the app
    with Session(engine) as session:
        for model in (AuthSession, AuthIdentity, AppUser):
            for row in session.exec(select(model)).all():
                session.delete(row)
        session.commit()


@pytest.fixture(autouse=True)
def no_account():
    _wipe()
    yield
    _wipe()


def _id_token(**claims) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


@pytest.fixture()
def provider(monkeypatch):
    """Google and Microsoft configured, the token endpoint answered locally."""
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "microsoft_client_id", CLIENT_ID)
    issued: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id_token": _id_token(**issued)})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(oauth.httpx, "AsyncClient", client)
    return issued


def _claims(provider_id: str, **extra) -> dict:
    iss = {"google": "https://accounts.google.com"}.get(provider_id, "https://login.example")
    return {"iss": iss, "aud": CLIENT_ID, "exp": time.time() + 600, "sub": "sub-1", **extra}


def test_an_unverified_google_address_is_dropped(provider):
    provider.update(_claims("google", email=OWNER, email_verified=False))
    assert asyncio.run(oauth.exchange("google", "code", "verifier"))["email"] is None


def test_a_verified_address_is_kept(provider):
    provider.update(_claims("google", email=OWNER, email_verified=True))
    assert asyncio.run(oauth.exchange("google", "code", "verifier"))["email"] == OWNER


def test_apple_spells_verified_as_a_string(provider, monkeypatch):
    monkeypatch.setattr(settings, "apple_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "apple_client_secret", "signed-assertion")
    provider.update({**_claims("apple", email=OWNER, email_verified="true"), "iss": "https://appleid.apple.com"})
    assert asyncio.run(oauth.exchange("apple", "code", "verifier"))["email"] == OWNER


def test_microsoft_does_not_vouch_for_its_email_claim(provider):
    provider.update(_claims("microsoft", email=OWNER))
    assert asyncio.run(oauth.exchange("microsoft", "code", "verifier"))["email"] is None


def test_an_unverified_owner_address_cannot_claim_the_account(client, db, provider):
    """End to end, before anyone has claimed the deployment."""
    ratelimit.reset()
    start = client.get("/api/auth/oauth/microsoft/start", follow_redirects=False)
    assert start.status_code == 307
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    provider.update(_claims("microsoft", email=OWNER))

    done = client.get(
        f"/api/auth/oauth/microsoft/callback?state={state}&code=x", follow_redirects=False
    )

    assert "auth_error=neni_vlastnik" in unquote(done.headers["location"])
    assert db.exec(select(AppUser)).first() is None
