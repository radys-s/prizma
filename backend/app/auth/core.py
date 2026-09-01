"""Passcode hashing and session issuing.

Deliberately dependency-free: `hashlib.scrypt` ships with CPython, so setting
this up can't be broken by the TLS/certificate problems that bite pip on this
machine. scrypt with n=2**14 is a memory-hard KDF — a leaked hash is not
brute-forceable at any speed that matters for a local passcode.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models.auth import AppUser, AuthSession, default_expiry, utcnow

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 32

SESSION_COOKIE = "ma_session"
CSRF_COOKIE = "ma_csrf"
CSRF_HEADER = "x-csrf-token"

# Four is Tomáš's explicit call, made with the tradeoff on the table: on a
# public URL a four-digit code is 10 000 possibilities. What makes it defensible
# is `app/auth/throttle.py` — a database-backed counter with a global bucket and
# exponential backoff, so guessing it is measured in years rather than hours —
# and `PASSCODE_LOGIN_ENABLED`, which switches the path off entirely once Google
# or e-mail is linked. Email passwords have their own, higher floor.
MIN_PASSCODE_LEN = 4

# ------------------------------------------------------------------ passcode


def hash_passcode(passcode: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        passcode.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_passcode(passcode: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            passcode.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(hash_hex)),
        )
    except (ValueError, TypeError):
        return False
    # Constant-time: a timing difference here leaks the hash prefix.
    return hmac.compare_digest(dk, bytes.fromhex(hash_hex))


# ------------------------------------------------------------------ sessions


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(session: Session, user_id: int) -> tuple[str, str]:
    """Returns (session_token, csrf_token). Only their hashes/values persist."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    session.add(
        AuthSession(
            token_hash=_hash_token(token),
            user_id=user_id,
            csrf_token=csrf,
            expires_at=default_expiry(),
        )
    )
    session.commit()
    return token, csrf


def resolve_session(session: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    row = session.exec(
        select(AuthSession).where(AuthSession.token_hash == _hash_token(token))
    ).first()
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:  # SQLite hands back naive UTC
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= utcnow():
        session.delete(row)
        session.commit()
        return None
    return row


def revoke_session(session: Session, token: str | None) -> None:
    row = resolve_session(session, token)
    if row is not None:
        session.delete(row)
        session.commit()


def revoke_all_for_user(session: Session, user_id: int) -> None:
    for row in session.exec(select(AuthSession).where(AuthSession.user_id == user_id)).all():
        session.delete(row)
    session.commit()


# --------------------------------------------------------------------- users


def get_user(session: Session) -> AppUser | None:
    """The app is single-user, so the first row is the account."""
    return session.exec(select(AppUser).order_by(AppUser.id)).first()


def has_account(session: Session) -> bool:
    return get_user(session) is not None


def utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)
