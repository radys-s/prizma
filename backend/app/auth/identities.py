"""Who owns the account, and which ways in belong to them.

Prizma is single-user by design, and that design survives round 4 unchanged:
there is exactly one `AppUser` row, and `core.get_user()` still returns it.
What changes is that the one account can be reached several ways — the access
code, Google, GitHub, an email address — each an `AuthIdentity` row.

The four rules, in the order they are checked:

1. **No account yet** → the first sign-in creates it, *if* `OWNER_EMAIL` is
   unset or matches. This is not a formality. The URL is public and the app
   answers to anyone who loads it; without this gate, whoever finds it first
   owns it, and Tomáš arrives to find himself locked out of his own app by a
   stranger's Google account.
2. **Identity already known** → sign in, refreshing the display name.
3. **Unknown identity, nobody signed in** → refuse with `jiny_ucet`. Adding it
   silently would mean anyone with any Google account could walk in.
4. **Unknown identity, already signed in** → link it. This is the only way a
   second sign-in method is ever added, and it requires already being inside.
"""

import re

from sqlmodel import Session, select

from app.config import settings
from app.models.auth import AppUser, AuthIdentity

LOCAL_PROVIDER = "local"
EMAIL_PROVIDER = "email"


class NotTheOwner(RuntimeError):
    """`OWNER_EMAIL` is set and this is not it."""


class DifferentAccount(RuntimeError):
    """An account exists and this identity is not one of its ways in."""


# Deliberately not `pydantic.EmailStr`: that pulls in `email-validator`, a
# dependency whose whole job here would be to reject addresses that Resend
# rejects anyway. This is the shape check — one @, a dot in the domain, no
# spaces, no control characters — which is what actually stops a header
# injection or an empty string reaching the mail API.
_EMAIL_RE = re.compile(r"^[^@\s,;<>\\\"]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}$")


def valid_email(email: str | None) -> bool:
    normalized = normalize_email(email)
    return normalized is not None and _EMAIL_RE.match(normalized) is not None


def normalize_email(email: str | None) -> str | None:
    return email.strip().lower() or None if email else None


def owner_allows(email: str | None) -> bool:
    """Whether this address may create the account.

    An unset `OWNER_EMAIL` allows anyone, which is correct on a laptop and
    dangerous on a server — `check_public_mode` does not force it because a
    deployment may legitimately be claimed by its first visitor and then
    closed. It is documented in `.env.example` as the thing to set before the
    URL is shared.
    """
    expected = normalize_email(settings.owner_email)
    if expected is None:
        return True
    return normalize_email(email) == expected


def find_identity(session: Session, provider: str, subject: str) -> AuthIdentity | None:
    return session.exec(
        select(AuthIdentity)
        .where(AuthIdentity.provider == provider)
        .where(AuthIdentity.subject == str(subject))
    ).first()


def identities_for(session: Session, user_id: int) -> list[AuthIdentity]:
    return list(
        session.exec(
            select(AuthIdentity)
            .where(AuthIdentity.user_id == user_id)
            .order_by(AuthIdentity.id)
        ).all()
    )


def resolve(
    session: Session,
    *,
    provider: str,
    subject: str,
    email: str | None = None,
    display_name: str | None = None,
    current_user: AppUser | None = None,
    secret_hash: str | None = None,
) -> AppUser:
    """The four rules above. Returns the account, or raises.

    `current_user` is the already-signed-in account when there is one, which is
    what turns rule 3 into rule 4. `secret_hash` is written onto the *identity*
    being created and never onto an identity that already exists — changing a
    credential is `set_secret()`, deliberately a separate call, so that a
    sign-in can never quietly rewrite the password it was checked against.
    """
    subject = str(subject)
    email = normalize_email(email)

    existing = find_identity(session, provider, subject)
    if existing is not None:
        user = session.get(AppUser, existing.user_id)
        if user is not None:
            if display_name and not existing.display_name:
                existing.display_name = display_name
                session.add(existing)
            if email and not existing.email:
                existing.email = email
                session.add(existing)
            if display_name and not user.display_name:
                user.display_name = display_name
                session.add(user)
            session.commit()
            session.refresh(user)
            return user
        # An identity whose user is gone is corruption, not a sign-in. Drop it
        # and fall through to the normal rules rather than returning None.
        session.delete(existing)
        session.commit()

    from app.auth import core

    account = core.get_user(session)

    if account is None:
        if not owner_allows(email):
            raise NotTheOwner(
                "Tenhle účet nemá k aplikaci přístup. Přihlas se adresou vlastníka."
            )
        account = AppUser(
            provider=provider,
            subject=subject,
            email=email,
            display_name=display_name or email,
            password_hash=secret_hash,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
    elif current_user is None or current_user.id != account.id:
        raise DifferentAccount("jiny_ucet")

    session.add(
        AuthIdentity(
            user_id=account.id,
            provider=provider,
            subject=subject,
            email=email,
            display_name=display_name,
            secret_hash=secret_hash,
        )
    )
    if email and not account.email:
        account.email = email
        session.add(account)
    if display_name and not account.display_name:
        account.display_name = display_name
        session.add(account)
    session.commit()
    session.refresh(account)
    return account


def set_secret(session: Session, user_id: int, provider: str, subject: str, secret_hash: str) -> AuthIdentity:
    """Set or replace the credential on one identity, creating it if needed.

    This is how the access code gets onto a server account that was claimed
    with Google: sign in, then set the code. It requires being signed in, which
    is what keeps it from being a password reset anyone can call.
    """
    identity = find_identity(session, provider, subject)
    if identity is None:
        identity = AuthIdentity(user_id=user_id, provider=provider, subject=str(subject))
    elif identity.user_id != user_id:
        raise DifferentAccount("jiny_ucet")

    identity.secret_hash = secret_hash
    session.add(identity)

    # Keep the legacy column in step for `local`, so an older code path or a
    # database opened by hand still shows the same access code.
    if provider == LOCAL_PROVIDER:
        user = session.get(AppUser, user_id)
        if user is not None:
            user.password_hash = secret_hash
            session.add(user)

    session.commit()
    session.refresh(identity)
    return identity


def local_secret(session: Session) -> str | None:
    """The access code's hash, wherever it currently lives.

    Falls back to `AppUser.password_hash` for a database that predates
    identities and has not been through `_backfill_identities()` yet.
    """
    from app.auth import core

    user = core.get_user(session)
    if user is None:
        return None
    identity = find_identity(session, LOCAL_PROVIDER, LOCAL_PROVIDER)
    if identity is not None and identity.secret_hash:
        return identity.secret_hash
    return user.password_hash


def unlink(session: Session, user_id: int, identity_id: int) -> AuthIdentity:
    """Remove one way in. Refuses to remove the last one.

    Without that refusal the settings panel offers a single click that locks
    the only user out of the only account, with no recovery path — the app has
    no support desk and no password reset that does not itself need an
    identity.
    """
    identity = session.get(AuthIdentity, identity_id)
    if identity is None or identity.user_id != user_id:
        raise LookupError("Taková přihlašovací metoda tu není.")
    if len(identities_for(session, user_id)) <= 1:
        raise ValueError("Tohle je poslední způsob přihlášení — odpojit ho nejde.")
    session.delete(identity)
    session.commit()
    return identity
