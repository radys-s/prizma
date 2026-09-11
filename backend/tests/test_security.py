"""The pre-launch security fixes, each pinned by the attack it prevents.

These are regression tests in the strict sense: every one of them fails against
the code as it was before the hardening pass, and each docstring names what an
attacker got out of it.
"""

from urllib.parse import unquote

import pytest
from sqlmodel import select

from app.auth import core, identities, oauth, throttle
from app.db import normalize_database_url
from app.models.auth import LoginAttempt, utcnow

ORIGIN = {"Origin": "https://testserver"}


# ----------------------------------------------------- OAuth linking (A1)


def test_a_callback_without_the_state_cookie_is_refused(client):
    """The account-takeover path. The callback is a GET, so it arrives with the
    owner's SameSite=Lax session cookie attached; before the cookie check, one
    crafted link linked an attacker's Google account to the owner's account and
    handed over every stored provider key."""
    res = client.get(
        "/api/auth/oauth/google/callback?state=forged&code=x", follow_redirects=False
    )
    assert res.status_code == 303
    assert "auth_error=" in res.headers["location"]


def test_a_callback_whose_cookie_disagrees_with_the_state_is_refused(client):
    res = client.get(
        "/api/auth/oauth/google/callback?state=two&code=x",
        headers={"Cookie": f"{oauth.STATE_COOKIE}=one"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "auth_error=" in res.headers["location"]


def test_a_matching_cookie_gets_past_the_csrf_check(client):
    """The control for the two tests above: with a matching cookie the request
    reaches the handshake table and is refused there instead, for an unknown
    state. Without this, a route that refused everything would pass them."""
    oauth._pending.clear()
    res = client.get(
        "/api/auth/oauth/google/callback?state=same&code=x",
        headers={"Cookie": f"{oauth.STATE_COOKIE}=same"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    location = unquote(res.headers["location"])
    assert "auth_error=" in location
    # Past the cookie check and refused by the handshake table instead.
    assert "prohlížeči" not in location
    assert "Neplatný nebo prošlý" in location


def test_the_pending_handshake_table_cannot_grow_without_bound():
    """It is filled by an unauthenticated GET, so an unbounded dict is a memory
    leak anyone can drive."""
    oauth._pending.clear()
    for i in range(oauth._MAX_PENDING + 50):
        oauth._pending[f"state-{i}"] = ("google", "verifier", 1e12, None)
        oauth._prune()
    assert len(oauth._pending) <= oauth._MAX_PENDING
    oauth._pending.clear()


def test_an_unknown_state_is_rejected_rather_than_returning_none():
    oauth._pending.clear()
    with pytest.raises(oauth.OAuthError):
        oauth.take_pending("never-issued")


# ------------------------------------------------- email registration (A3)


def test_registering_a_known_address_with_the_wrong_password_sends_no_mail(
    client, db, monkeypatch
):
    """`identities.resolve` returns an existing identity without ever checking
    its secret, so this route used to mail a live login code to the owner's
    address for anyone who typed the address and any password at all — both a
    takeover assist and a way to burn Resend's 100 mails a day."""
    email = "owner@example.com"
    user = identities.resolve(
        db,
        provider=identities.EMAIL_PROVIDER,
        subject=email,
        email=email,
        secret_hash=core.hash_passcode("SpravneHeslo1"),
    )
    assert user is not None

    sent: list[str] = []

    async def spy(to, subject, text):
        sent.append(to)
        return True

    monkeypatch.setattr("app.mail.send", spy)
    throttle.clear_failures(db, "testclient")

    res = client.post(
        "/api/auth/email/register",
        json={"email": email, "password": "ZcelaJineHeslo9"},
        headers=ORIGIN,
    )

    assert res.status_code >= 400
    assert sent == []


def test_the_mail_cooldown_blocks_an_immediate_resend():
    """One address, one code a minute. Without it the route is a mail cannon
    pointed at the owner's inbox and at the day's quota."""
    from app.api import auth as auth_api

    auth_api._last_mailed.clear()
    assert auth_api._mail_cooldown_left("a@example.com") == 0

    import time as _time

    auth_api._last_mailed["a@example.com"] = _time.monotonic()
    assert auth_api._mail_cooldown_left("a@example.com") > 0


# ------------------------------------------------------- lockout decay (A4)


def test_the_global_lockout_forgives_itself_after_a_quiet_spell(db):
    """The global counter never decayed and `clear_failures` deliberately does
    not reset it, so one wrong guess every two hours pinned the two-hour
    ceiling forever — a denial of service anyone could run from a cron job."""
    for _ in range(throttle.GLOBAL_FREE_ATTEMPTS + 3):
        throttle.record_failure(db, "10.0.0.1")

    row = db.get(LoginAttempt, throttle.GLOBAL_SCOPE)
    assert row.failures > throttle.GLOBAL_FREE_ATTEMPTS

    # Backdate the bucket past its quiet window, as a real gap would.
    from datetime import timedelta

    row.updated_at = utcnow() - timedelta(seconds=throttle.GLOBAL_QUIET_RESET_SECONDS + 60)
    db.add(row)
    db.commit()

    throttle.record_failure(db, "10.0.0.2")
    row = db.get(LoginAttempt, throttle.GLOBAL_SCOPE)
    assert row.failures == 1
    assert row.locked_until is None

    for scope in db.exec(select(LoginAttempt)).all():
        db.delete(scope)
    db.commit()


# ------------------------------------------------ bodies and limits (A5)


def test_a_chat_message_has_a_ceiling():
    """`EditRequest.content` was capped and `ChatRequest.message` was not, so
    one authenticated request could hand a 512 MB instance an arbitrarily large
    string."""
    from pydantic import ValidationError

    from app.api.chat import ChatRequest

    assert ChatRequest(message="x" * 32000)
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 32001)
    with pytest.raises(ValidationError):
        ChatRequest(message="")


def test_an_oversized_body_is_refused_before_it_is_read(client):
    from app.auth.hardening import MAX_BODY_BYTES

    res = client.post(
        "/api/auth/email/register",
        content=b"x" * (MAX_BODY_BYTES + 1024),
        headers={**ORIGIN, "Content-Type": "application/json"},
    )
    assert res.status_code == 413


def test_the_rate_limiter_lets_the_first_calls_through_and_then_stops(monkeypatch):
    from app.auth import ratelimit

    ratelimit.reset()
    for _ in range(5):
        ratelimit.check("probe", "10.0.0.9", limit=5, window=60.0)
    with pytest.raises(ratelimit.RateLimited) as exc:
        ratelimit.check("probe", "10.0.0.9", limit=5, window=60.0)
    assert exc.value.retry_after > 0
    # Per IP, not global: a second visitor must not inherit the first's ban.
    ratelimit.check("probe", "10.0.0.10", limit=5, window=60.0)
    ratelimit.reset()


# ------------------------------------------------------ host and headers


def test_a_foreign_host_header_is_refused(client):
    """A Host the app does not answer for is either a misconfiguration or a
    cache-poisoning attempt; 421 says "wrong server", which is what it is."""
    res = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert res.status_code == 421


def test_public_mode_sends_hsts_and_the_rest_of_the_header_set(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert "max-age=31536000" in res.headers["strict-transport-security"]
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"]


def test_the_api_docs_are_closed_in_public_mode(client):
    """Everything behind them needs auth, but an anonymous visitor got an
    interactive map of a private app.

    The status code is 200, not 404: the SPA catch-all answers anything it does
    not recognise with the app shell. What matters is that no schema and no
    Swagger page is served, so that is what this asserts."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        res = client.get(path)
        assert "application/json" not in res.headers.get("content-type", "")
        assert "swagger" not in res.text.lower()
        assert '"openapi"' not in res.text


def test_robots_disallows_everything(client):
    res = client.get("/robots.txt")
    assert res.status_code == 200
    assert "Disallow: /" in res.text


# ------------------------------------------------------- database URL (B1)


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@ep-x.eu-central-1.aws.neon.tech/db",
        "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db",
        "postgresql+psycopg2://u:p@ep-x.eu-central-1.aws.neon.tech/db",
    ],
)
def test_every_form_neons_copy_button_produces_boots(raw):
    """Only `postgresql+psycopg://` works, because only psycopg 3 is installed.
    The other two crash-loop *after* the image has built."""
    url = normalize_database_url(raw)
    assert url.startswith("postgresql+psycopg://")
    assert "sslmode=require" in url


def test_an_existing_sslmode_is_left_alone():
    url = normalize_database_url("postgresql://u:p@host.neon.tech/db?sslmode=verify-full")
    assert url.count("sslmode") == 1
    assert "verify-full" in url


def test_a_local_database_is_not_forced_through_tls():
    assert normalize_database_url("postgresql://u:p@localhost/db") == (
        "postgresql+psycopg://u:p@localhost/db"
    )


def test_sqlite_and_empty_are_passed_through_untouched():
    assert normalize_database_url("sqlite:///./data/app.db") == "sqlite:///./data/app.db"
    assert normalize_database_url("") == ""


# ------------------------------------------------------ configuration (A2)


def test_a_blank_owner_email_fails_the_boot_check():
    """`owner_allows()` returns True when it is empty and `render.yaml` marks
    it `sync: false`, so between deploy and first sign-in the first stranger to
    find the URL could claim the account with a four-digit code."""
    from app.config import ConfigError, Settings, check_public_mode

    good = Settings(
        public_mode=True,
        secret_key="0" * 64,
        allowed_hosts=["prizma.example.com"],
        public_base_url="https://prizma.example.com",
        owner_email="owner@example.com",
    )
    check_public_mode(good)  # must not raise

    with pytest.raises(ConfigError) as exc:
        check_public_mode(
            Settings(
                public_mode=True,
                secret_key="0" * 64,
                allowed_hosts=["prizma.example.com"],
                public_base_url="https://prizma.example.com",
                owner_email="   ",
            )
        )
    assert "OWNER_EMAIL" in str(exc.value)


def test_the_allowed_host_is_derived_from_the_public_url_when_unset():
    """`render.yaml` hardcoded the hostname in three places; a mismatch made
    the middleware answer 421 to every request, health check included."""
    from app.config import Settings

    derived = Settings(public_base_url="https://prizma-abc12.onrender.com", allowed_hosts=[])
    assert derived.effective_allowed_hosts() == ["prizma-abc12.onrender.com"]

    explicit = Settings(
        public_base_url="https://prizma.onrender.com", allowed_hosts=["custom.example.com"]
    )
    assert explicit.effective_allowed_hosts() == ["custom.example.com"]


def test_locally_a_correct_code_unlocks_the_global_bucket(db, monkeypatch):
    """Na `127.0.0.1` počítají obě počítadla téhož člověka.

    Globální bucket má zdržet rozprostřené hádání proti veřejné adrese. Lokálně
    ale „kdokoli s platným přístupem" a „ten, kdo hádá" je tentýž uživatel, tak
    mu třináct překlepů za šest hodin zamklo aplikaci na dvě hodiny — a správný
    kód s tím nehnul, protože `clear_failures` se globálního bucketu zásadně
    nedotýkal. Jediná cesta ven vedla přes ruční zásah do databáze.
    """
    throttle.reset(db)
    monkeypatch.setattr(throttle.settings, "public_mode", False)
    for _ in range(throttle.GLOBAL_FREE_ATTEMPTS + 3):
        throttle.record_failure(db, "127.0.0.1")
    assert throttle.lockout_remaining(db, "127.0.0.1") > 0

    throttle.clear_failures(db, "127.0.0.1")
    assert throttle.lockout_remaining(db, "127.0.0.1") == 0
    assert db.get(LoginAttempt, throttle.GLOBAL_SCOPE) is None


def test_publicly_a_correct_code_still_leaves_the_global_bucket_alone(db, monkeypatch):
    """Na veřejné adrese by to byl reset button pro útočníka."""
    throttle.reset(db)
    monkeypatch.setattr(throttle.settings, "public_mode", True)
    for _ in range(throttle.GLOBAL_FREE_ATTEMPTS + 3):
        throttle.record_failure(db, "10.0.0.7")

    throttle.clear_failures(db, "10.0.0.7")
    assert db.get(LoginAttempt, "10.0.0.7") is None
    assert db.get(LoginAttempt, throttle.GLOBAL_SCOPE) is not None
    throttle.reset(db)
