"""The e-mail password must never work as the access code.

An account created by `/email/register` stored the password hash on
`AppUser.password_hash` as well as on its e-mail identity. That column is the
legacy home of the access code, and `identities.local_secret()` falls back to it
when no access code has been set — so `POST /api/auth/login` accepted the
e-mail password and issued a session with no second factor at all. Before the
account was claimed, that let anyone who knew `OWNER_EMAIL` register with a
password of their choosing and sign straight in.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.auth import core, identities, throttle
from app.db import engine, init_db
from app.models.auth import (
    AppUser,
    AuthIdentity,
    AuthSession,
    EmailChallenge,
    LoginAttempt,
    RecoveryCode,
)

ORIGIN = {"Origin": "https://testserver"}
OWNER = "owner@example.com"
PASSWORD = "Heslo-k-mailu-123"


def _wipe() -> None:
    init_db()  # the tables exist only once something has started the app
    with Session(engine) as session:
        for model in (AuthSession, EmailChallenge, RecoveryCode, AuthIdentity, LoginAttempt, AppUser):
            for row in session.exec(select(model)).all():
                session.delete(row)
        session.commit()


@pytest.fixture(autouse=True)
def no_account():
    _wipe()
    yield
    _wipe()


@pytest.fixture()
def mailed(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def spy(to, subject, text):
        sent.append((to, text))
        return True

    monkeypatch.setattr("app.mail.send", spy)
    return sent


def _login(client, passcode: str):
    return client.post("/api/auth/login", json={"passcode": passcode}, headers=ORIGIN)


def test_the_email_password_does_not_open_the_access_code_login(client, mailed):
    res = client.post(
        "/api/auth/email/register",
        json={"email": OWNER, "password": PASSWORD},
        headers=ORIGIN,
    )
    assert res.status_code == 200, res.text
    assert res.json()["stage"] == "2fa"

    login = _login(client, PASSWORD)

    assert login.status_code == 401
    assert core.SESSION_COOKIE not in login.cookies


def test_an_account_created_by_email_has_no_access_code(db):
    user = identities.resolve(
        db,
        provider=identities.EMAIL_PROVIDER,
        subject=OWNER,
        email=OWNER,
        secret_hash=core.hash_passcode(PASSWORD),
    )

    assert user.password_hash is None
    assert identities.local_secret(db) is None


def test_a_database_written_before_the_fix_is_covered_too(client, db):
    """Existing deployments already hold the e-mail password in the column."""
    hashed = core.hash_passcode(PASSWORD)
    user = AppUser(provider="email", subject=OWNER, email=OWNER, password_hash=hashed)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(AuthIdentity(user_id=user.id, provider="email", subject=OWNER, email=OWNER, secret_hash=hashed))
    db.commit()

    assert _login(client, PASSWORD).status_code == 401


def test_an_access_code_from_before_identities_still_works(client, db):
    """The fallback exists for this account: one that began with the access
    code and whose legacy provider was later overwritten by a Google link."""
    user = AppUser(provider="google", subject="g-1", password_hash=core.hash_passcode("4821"))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(AuthIdentity(user_id=user.id, provider="google", subject="g-1"))
    db.commit()
    throttle.reset(db)

    assert _login(client, "4821").status_code == 200


def test_the_access_code_set_from_inside_still_works(client, db):
    user = identities.resolve(
        db,
        provider=identities.EMAIL_PROVIDER,
        subject=OWNER,
        email=OWNER,
        secret_hash=core.hash_passcode(PASSWORD),
    )
    identities.set_secret(
        db, user.id, identities.LOCAL_PROVIDER, identities.LOCAL_PROVIDER, core.hash_passcode("4821")
    )
    throttle.reset(db)

    assert _login(client, "4821").status_code == 200


def test_the_recovery_script_sets_a_code_that_signs_in(client, db):
    """`scripts/set_passcode.py` wrote the legacy column directly, which only
    worked through the fallback closed above, and then called a function that
    does not exist."""
    identities.resolve(
        db,
        provider=identities.EMAIL_PROVIDER,
        subject=OWNER,
        email=OWNER,
        secret_hash=core.hash_passcode(PASSWORD),
    )
    path = Path(__file__).resolve().parents[2] / "scripts" / "set_passcode.py"
    spec = importlib.util.spec_from_file_location("set_passcode", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    script.apply("4821")
    throttle.reset(db)

    assert _login(client, "4821").status_code == 200
    assert _login(client, PASSWORD).status_code == 401
