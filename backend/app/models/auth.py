from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_expiry() -> datetime:
    return utcnow() + timedelta(days=30)


class AppUser(SQLModel, table=True):
    """The single account. Still exactly one row, deliberately.

    `provider`/`subject` are **legacy**. They used to answer "how do you sign
    in", which only works while the answer is one thing: linking Google to a
    passcode account overwrote them, and from then on the passcode was the only
    way in — or Google was, depending on which had been used last. Sign-in
    methods now live in `AuthIdentity`, one row each, and these two columns are
    kept only so existing databases keep loading.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    subject: str = Field(index=True)
    email: Optional[str] = None
    display_name: Optional[str] = None
    # scrypt, never reversible. The access code, or the email password.
    password_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)

    # Whether a code sent to `email` has ever been typed back in. An address
    # nobody has proven they can read is not a second factor and cannot be used
    # to recover the account.
    email_verified: bool = False

    # RFC 6238 shared secret, base32, and whether the user has proved their
    # authenticator app agrees. Unconfirmed secrets are ignored at login — a
    # half-finished setup must not be able to lock its owner out.
    totp_secret: Optional[str] = None
    totp_confirmed: bool = False


class AuthIdentity(SQLModel, table=True):
    """One way of signing in. A user may have several.

    `subject` is whatever the provider considers a stable id — a numeric
    GitHub id, a Google `sub`, the lower-cased address for `email`, the literal
    "local" for the access code. Never a display email for an OAuth provider:
    those change, and an account that follows a changed address follows it to
    whoever bought the domain next.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="appuser.id", index=True)
    provider: str = Field(index=True)
    subject: str = Field(index=True)
    email: Optional[str] = None
    display_name: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)

    # The secret belonging to *this* identity, scrypt-hashed. Set for `local`
    # (the four-digit access code) and `email` (the password); always None for
    # an OAuth provider, which has no secret of ours to keep.
    #
    # It lives here rather than on `AppUser` because the two are genuinely
    # different credentials for the same account: sharing one column would mean
    # setting an email password silently changes the access code to that
    # password, and vice versa.
    secret_hash: Optional[str] = None


class EmailChallenge(SQLModel, table=True):
    """A one-time code mailed to the account's address.

    Only the SHA-256 of the code is stored. It is six digits, so a hash is not
    much protection against someone who already has the database — but it does
    mean a code cannot be read out of a backup or a log dump, and the row is
    burned after five wrong guesses, which is what actually bounds it.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="appuser.id", index=True)
    purpose: str  # "verify" (prove the address) | "login" (second factor)
    code_hash: str
    email: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    attempts: int = 0
    consumed_at: Optional[datetime] = None
    last_sent_at: datetime = Field(default_factory=utcnow)


class RecoveryCode(SQLModel, table=True):
    """A single-use way back in when the authenticator app is gone.

    Hashed with scrypt rather than SHA-256, unlike `EmailChallenge`: these are
    long-lived and high-entropy, so they are much closer to a password than to
    a six-digit code that expires in ten minutes.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="appuser.id", index=True)
    code_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    used_at: Optional[datetime] = None


class LoginAttempt(SQLModel, table=True):
    """Failed sign-in counters, in the database rather than in memory.

    In-memory counters were fine on a laptop, where the only attacker was
    someone already sitting at it. On a public URL they are not: a restart
    clears them, and the four-digit access code has only 10 000 possibilities.

    `scope` is either an IP or the literal "global". The global bucket is the
    one that matters — an attacker rotating addresses resets every per-IP
    counter and would otherwise face no limit at all.
    """

    scope: str = Field(primary_key=True)
    failures: int = 0
    locked_until: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


class AuthSession(SQLModel, table=True):
    """A logged-in browser session.

    Only the SHA-256 of the token is stored: a leaked database file still
    doesn't let anyone mint a working cookie.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    user_id: int = Field(foreign_key="appuser.id", index=True)
    csrf_token: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=default_expiry)
