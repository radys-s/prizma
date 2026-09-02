"""Six-digit codes mailed to the account's address, and recovery codes.

A six-digit code is 1 000 000 possibilities, which sounds like a lot and is
not: at a thousand guesses a second it falls in under twenty minutes. What
makes it safe is everything around it — the code is single use, dies after ten
minutes, and the row is burned after five wrong guesses. Those three together
give an attacker five tries at one-in-a-million, which is the actual number.

Codes are stored as SHA-256, not scrypt. That is a deliberate difference from
passwords: these live for ten minutes and are rate-limited to five guesses, so
the slow KDF would buy nothing, and it *would* be paid on every verification —
including by an attacker, which is how a login endpoint becomes a way to burn
the server's CPU.
"""

import hashlib
import secrets
from datetime import timedelta, timezone

from sqlmodel import Session, select

from app.auth import core
from app.models.auth import EmailChallenge, RecoveryCode, utcnow

CODE_DIGITS = 6
TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5

# How long before a "poslat znovu" click does anything. Without it the resend
# button is an unmetered way to send mail from Tomáš's Resend quota to his own
# inbox — 100 messages a day, and the free tier stops there.
RESEND_COOLDOWN = timedelta(seconds=30)

RECOVERY_COUNT = 8
RECOVERY_GROUPS = 3
RECOVERY_GROUP_LEN = 4
# No I, O, 0 or 1: these get read off a screen and typed by hand.
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

PURPOSE_VERIFY = "verify"
PURPOSE_LOGIN = "login"

# A challenge whose code is never mailed and never checked: the second factor
# is an authenticator app instead. The row exists so that the TOTP step is
# *bound to a password check* — without it, `POST /totp/verify` would be a way
# in that needs only a six-digit code and no password at all.
PURPOSE_TOTP = "totp"

# Purposes whose code arrives by email, and which `/email/verify` will accept.
MAILED_PURPOSES = (PURPOSE_VERIFY, PURPOSE_LOGIN)


class ChallengeError(RuntimeError):
    """Wrong, expired, replayed or exhausted. The message is user-facing."""


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_code() -> str:
    return str(secrets.randbelow(10**CODE_DIGITS)).zfill(CODE_DIGITS)


def create(session: Session, user_id: int, email: str, purpose: str) -> tuple[EmailChallenge, str]:
    """A new challenge and its plaintext code. The code is never stored.

    Any earlier unconsumed challenge for the same purpose is dropped first, so
    two codes are never valid at once — otherwise a "poslat znovu" that mints a
    second row leaves the first one live, and the older code in the older email
    still works.
    """
    stale = session.exec(
        select(EmailChallenge)
        .where(EmailChallenge.user_id == user_id)
        .where(EmailChallenge.purpose == purpose)
        .where(EmailChallenge.consumed_at == None)  # noqa: E711 - SQL NULL, not Python None
    ).all()
    for row in stale:
        session.delete(row)

    code = generate_code()
    challenge = EmailChallenge(
        user_id=user_id,
        purpose=purpose,
        code_hash=_hash(code),
        email=email,
        expires_at=core.utc_naive(utcnow() + TTL),
    )
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return challenge, code


def _aware(value):
    """SQLite returns naive UTC datetimes; Postgres returns aware ones."""
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def load(session: Session, challenge_id: int) -> EmailChallenge:
    challenge = session.get(EmailChallenge, challenge_id)
    if challenge is None:
        raise ChallengeError("Ověření vypršelo. Zkus přihlášení znovu.")
    return challenge


def check_usable(session: Session, challenge: EmailChallenge) -> None:
    """Raise unless the challenge is still open. Expired rows are dropped."""
    if challenge.consumed_at is not None:
        raise ChallengeError("Tenhle kód už byl použitý. Nech si poslat nový.")
    if _aware(challenge.expires_at) <= utcnow():
        session.delete(challenge)
        session.commit()
        raise ChallengeError("Kód vypršel. Nech si poslat nový.")


def consume(session: Session, challenge: EmailChallenge) -> EmailChallenge:
    """Burn the challenge without checking a code.

    Used by the TOTP path, where the second factor is the authenticator app and
    this row exists only to prove the password step happened.
    """
    challenge.consumed_at = core.utc_naive(utcnow())
    session.add(challenge)
    session.commit()
    return challenge


def verify(session: Session, challenge_id: int, code: str) -> EmailChallenge:
    """Consume the challenge if `code` is right. Raises otherwise."""
    challenge = load(session, challenge_id)
    check_usable(session, challenge)

    cleaned = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(cleaned) == CODE_DIGITS and secrets.compare_digest(_hash(cleaned), challenge.code_hash):
        challenge.consumed_at = core.utc_naive(utcnow())
        session.add(challenge)
        session.commit()
        return challenge

    challenge.attempts += 1
    if challenge.attempts >= MAX_ATTEMPTS:
        # Burn it rather than leaving it to expire: five wrong guesses is not a
        # typo, and every remaining second is another second of guessing.
        session.delete(challenge)
        session.commit()
        raise ChallengeError("Příliš mnoho pokusů. Nech si poslat nový kód.")

    session.add(challenge)
    session.commit()
    left = MAX_ATTEMPTS - challenge.attempts
    raise ChallengeError(f"Nesprávný kód. Zbývají {left} pokusy.")


def may_resend(challenge: EmailChallenge) -> int:
    """Seconds until a resend is allowed. 0 means now."""
    ready = _aware(challenge.last_sent_at) + RESEND_COOLDOWN
    return max(0, int((ready - utcnow()).total_seconds()))


def rotate_code(session: Session, challenge: EmailChallenge) -> str:
    """A new code on the *same* row, so the old one stops working.

    Minting a second challenge instead would leave two valid codes in two
    emails, which quietly doubles an attacker's odds for every resend.
    """
    code = generate_code()
    challenge.code_hash = _hash(code)
    challenge.attempts = 0
    challenge.expires_at = core.utc_naive(utcnow() + TTL)
    challenge.last_sent_at = core.utc_naive(utcnow())
    session.add(challenge)
    session.commit()
    return code


# ------------------------------------------------------------ recovery codes


def _format_recovery() -> str:
    groups = [
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP_LEN))
        for _ in range(RECOVERY_GROUPS)
    ]
    return "-".join(groups)


def issue_recovery(session: Session, user_id: int) -> list[str]:
    """Replace the user's recovery codes. Returns them once, in plaintext."""
    for row in session.exec(
        select(RecoveryCode).where(RecoveryCode.user_id == user_id)
    ).all():
        session.delete(row)

    codes = [_format_recovery() for _ in range(RECOVERY_COUNT)]
    for code in codes:
        session.add(RecoveryCode(user_id=user_id, code_hash=core.hash_passcode(code)))
    session.commit()
    return codes


def redeem_recovery(session: Session, user_id: int, code: str) -> bool:
    """Spend one recovery code. False if it does not match an unused one."""
    cleaned = (code or "").strip().upper()
    if not cleaned:
        return False
    rows = session.exec(
        select(RecoveryCode)
        .where(RecoveryCode.user_id == user_id)
        .where(RecoveryCode.used_at == None)  # noqa: E711
    ).all()
    for row in rows:
        if core.verify_passcode(cleaned, row.code_hash):
            row.used_at = core.utc_naive(utcnow())
            session.add(row)
            session.commit()
            return True
    return False


def recovery_remaining(session: Session, user_id: int) -> int:
    return len(
        session.exec(
            select(RecoveryCode)
            .where(RecoveryCode.user_id == user_id)
            .where(RecoveryCode.used_at == None)  # noqa: E711
        ).all()
    )


def clear_recovery(session: Session, user_id: int) -> None:
    """Drop every recovery code, used or not.

    Called when the second factor is switched off. `issue_recovery` already
    replaces them on the way in, so this is not what stops an old code from
    working after a re-enable — it is that a user who turns 2FA off expects the
    slip of paper they wrote the codes on to become worthless right then, and
    not to sit in the database as live credentials for a factor that no longer
    exists.
    """
    for row in session.exec(
        select(RecoveryCode).where(RecoveryCode.user_id == user_id)
    ).all():
        session.delete(row)
    session.commit()
