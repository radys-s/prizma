"""Brute-force throttling that survives a restart and IP rotation.

The old version counted failures in a process dictionary, keyed by IP, and
locked out for a flat five minutes. On a laptop that was enough — the only
attacker who could reach the port was already sitting at the keyboard.

On a public URL neither property holds:

* **A restart cleared the counters.** Free-tier hosting restarts constantly:
  Render spins the service down after fifteen idle minutes. An attacker did not
  even need to cause the restart, only to wait for one.
* **Per-IP counting is per-attacker-choice.** Rotating addresses is cheap and
  resets the counter every time, so the limit bound nothing.

So: counters live in the database, and every failure increments **two** rows —
the caller's IP and a `global` bucket. The global one is what actually stops a
distributed guess at a four-digit code. Backoff is exponential, because the
honest tradeoff is that a real person mistyping twice should wait seconds while
someone on their twentieth attempt should wait hours.
"""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models.auth import LoginAttempt, utcnow

GLOBAL_SCOPE = "global"

# Failures before any waiting starts. Four fits "typed it wrong, tried again,
# checked caps lock, tried once more".
FREE_ATTEMPTS = 4

# Seconds to wait after failure 5, 6, 7, 8, and 9-or-more. Two hours is the
# ceiling on purpose: a permanent lock would be a denial of service anyone
# could trigger against Tomáš by guessing wrong on purpose.
BACKOFF_SECONDS = (5, 30, 300, 1800, 7200)

# The global bucket forgets faster in count but not in consequence: a genuine
# user's own failures land in it too, so it starts backing off later.
GLOBAL_FREE_ATTEMPTS = 12

# How long a bucket must stay quiet before its counter is forgiven.
#
# Without this the global counter only ever grows: `clear_failures` refuses to
# touch it (correctly — a valid sign-in must not be a reset button for someone
# else's guessing), so one wrong guess every two hours pins the 7200 s ceiling
# forever and locks Tomáš out of his own app permanently. A denial of service
# anyone could trigger with a cron job is not a defence.
#
# The windows are the tradeoff. Six quiet hours buy an attacker 12 fresh
# attempts, i.e. 48 a day: still ~200 days of uninterrupted guessing at a
# four-digit code, and the real answer to *that* is to turn
# `PASSCODE_LOGIN_ENABLED` off once a real identity is linked.
QUIET_RESET_SECONDS = 3600
GLOBAL_QUIET_RESET_SECONDS = 6 * 3600


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; Postgres does not."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _row(session: Session, scope: str) -> LoginAttempt:
    row = session.get(LoginAttempt, scope)
    if row is None:
        row = LoginAttempt(scope=scope)
        session.add(row)
    return row


def _remaining(row: LoginAttempt | None) -> int:
    locked = _aware(row.locked_until) if row else None
    if locked is None:
        return 0
    return max(0, int((locked - utcnow()).total_seconds()))


def lockout_remaining(session: Session, ip: str) -> int:
    """Seconds this caller must wait. The stricter of their bucket and global."""
    return max(
        _remaining(session.get(LoginAttempt, ip)),
        _remaining(session.get(LoginAttempt, GLOBAL_SCOPE)),
    )


def record_failure(session: Session, ip: str) -> int:
    """Count one failed attempt in both buckets. -> seconds now locked out."""
    now = utcnow()
    for scope, free, quiet in (
        (ip, FREE_ATTEMPTS, QUIET_RESET_SECONDS),
        (GLOBAL_SCOPE, GLOBAL_FREE_ATTEMPTS, GLOBAL_QUIET_RESET_SECONDS),
    ):
        row = _row(session, scope)
        last = _aware(row.updated_at)
        if last is not None and (now - last).total_seconds() >= quiet:
            row.failures = 0
            row.locked_until = None
        row.failures += 1
        row.updated_at = now
        over = row.failures - free
        if over > 0:
            wait = BACKOFF_SECONDS[min(over - 1, len(BACKOFF_SECONDS) - 1)]
            row.locked_until = now + timedelta(seconds=wait)
        session.add(row)
    session.commit()
    return lockout_remaining(session, ip)


def clear_failures(session: Session, ip: str) -> None:
    """A successful sign-in clears this caller — but not the global bucket.

    Clearing global on success would hand an attacker a reset button: anyone
    who knows *any* valid way in could wipe the counter that is bounding their
    guesses at another. The global bucket is drained by time instead — see
    `GLOBAL_QUIET_RESET_SECONDS`.
    """
    row = session.get(LoginAttempt, ip)
    if row is not None:
        session.delete(row)
        session.commit()


def reset(session: Session) -> None:
    """Test hook — clears every bucket."""
    for row in session.exec(select(LoginAttempt)).all():
        session.delete(row)
    session.commit()
